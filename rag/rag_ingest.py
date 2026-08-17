"""
RAG ingestion and retrieval for Foundry's "understand this repo" feature.

Chunking is AST-aware (see ast_chunker.py) — splits on real function/class
boundaries using tree-sitter, not arbitrary character counts. Falls back to
a plain sliding character window only for languages without a tree-sitter
grammar available, or files that fail to parse.

Vectors are stored as JSON float lists in a normal SQLite table, and
similarity search is cosine similarity computed in Python at query time —
not a real vector index. This is genuinely fine at hundreds-to-low-thousands
of chunks (one repo's worth); a real vector index (pgvector) is the right
upgrade once that stops being true, not before.
"""

import os
import re
import json
import sqlite3
import math
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from .github_import import fetch_github_codebase, GitHubImportError
from .ast_chunker import ast_chunk_file, _char_chunks as fallback_char_chunks

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

DB_PATH = "app.db"
EMBED_MODEL = "gemini-embedding-001"
CHAT_MODEL = "gemini-3.5-flash"  # matches the model already used elsewhere in this app

EMBED_BATCH_SIZE = 20  # keep embed_content payloads modest
TOP_K = 6

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client()  # reads GEMINI_API_KEY from env, same as the rest of the app
    return _client


def init_rag_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo TEXT NOT NULL,
            path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT NOT NULL,
            node_type TEXT,
            name TEXT,
            start_line INTEGER,
            end_line INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_repo ON rag_chunks(repo)")
    conn.commit()
    conn.close()


def chunk_file(path: str, content: str) -> list[dict]:
    """AST-aware chunking first; falls back to a plain character window
    for languages tree-sitter doesn't have a grammar for, or files that
    fail to parse."""
    ast_chunks = ast_chunk_file(path, content)
    if ast_chunks is not None:
        return ast_chunks
    return fallback_char_chunks(content)


def _embed_batch(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    result = client.models.embed_content(model=EMBED_MODEL, contents=texts)
    return [e.values for e in result.embeddings]


def ingest_repo(repo_full_name: str, token: str | None = None) -> dict:
    """Fetches a repo, chunks every file (AST-aware where possible), embeds
    each chunk, and stores it. Re-ingesting the same repo clears its old
    chunks first, so this is safe to call again after the repo changes."""
    try:
        context = fetch_github_codebase(f"github.com/{repo_full_name}", token=token)
    except GitHubImportError:
        raise

    records = []  # (path, chunk_index, chunk_dict)
    for path, content in context["file_full_contents"].items():
        for i, chunk in enumerate(chunk_file(path, content)):
            records.append((path, i, chunk))

    if not records:
        return {"files_processed": len(context["file_tree"]), "chunks_stored": 0}

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM rag_chunks WHERE repo = ?", (repo_full_name,))

    stored = 0
    for batch_start in range(0, len(records), EMBED_BATCH_SIZE):
        batch = records[batch_start:batch_start + EMBED_BATCH_SIZE]
        texts = [r[2]["content"] for r in batch]
        vectors = _embed_batch(texts)
        for (path, chunk_index, chunk), vector in zip(batch, vectors):
            conn.execute(
                """INSERT INTO rag_chunks
                   (repo, path, chunk_index, content, embedding, node_type, name, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (repo_full_name, path, chunk_index, chunk["content"], json.dumps(vector),
                 chunk.get("node_type"), chunk.get("name"), chunk.get("start_line"), chunk.get("end_line")),
            )
            stored += 1

    conn.commit()
    conn.close()
    return {"files_processed": len(context["file_tree"]), "chunks_stored": stored}


def repo_is_ingested(repo_full_name: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT 1 FROM rag_chunks WHERE repo = ? LIMIT 1", (repo_full_name,)).fetchone()
    conn.close()
    return row is not None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve_relevant_chunks(repo_full_name: str, query: str, top_k: int = TOP_K) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT path, chunk_index, content, embedding, node_type, name FROM rag_chunks WHERE repo = ?",
        (repo_full_name,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    query_vector = _embed_batch([query])[0]
    scored = []
    for path, chunk_index, content, embedding_json, node_type, name in rows:
        vector = json.loads(embedding_json)
        score = _cosine_similarity(query_vector, vector)
        scored.append({
            "path": path, "chunk_index": chunk_index, "content": content,
            "node_type": node_type, "name": name, "score": score,
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:top_k]


def answer_question(repo_full_name: str, question: str) -> dict:
    chunks = retrieve_relevant_chunks(repo_full_name, question)
    if not chunks:
        return {
            "answer": "This repo hasn't been ingested yet, or nothing relevant was found. Try ingesting it first.",
            "sources": [],
        }

    context_block = "\n\n".join(
        f"--- {c['path']}" + (f" ({c['name']})" if c.get("name") else "") + f" ---\n{c['content']}"
        for c in chunks
    )
    prompt = (
        f"You are answering a question about the codebase '{repo_full_name}' using only the "
        f"code excerpts below. If the excerpts don't contain enough information to answer "
        f"confidently, say so instead of guessing.\n\n"
        f"CODE EXCERPTS:\n{context_block}\n\n"
        f"QUESTION: {question}"
    )

    client = _get_client()
    response = client.models.generate_content(model=CHAT_MODEL, contents=prompt)

    return {
        "answer": response.text,
        "sources": [{"path": c["path"], "name": c.get("name"), "score": round(c["score"], 3)} for c in chunks],
    }
