import sqlite3
import os
import json

DB_PATH = "app.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codebase_files (
            codebase_id TEXT NOT NULL,
            path TEXT NOT NULL,
            content TEXT NOT NULL,
            PRIMARY KEY (codebase_id, path)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS codebase_context (
            codebase_id TEXT PRIMARY KEY,
            context_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brd_store (
            doc_id TEXT PRIMARY KEY,
            brd_json TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    print(f"[db] initialized at {os.path.abspath(DB_PATH)}")


def save_codebase_files(codebase_id: str, files: dict[str, str]):
    """files: {relative_path: full_content}"""
    conn = get_connection()
    for path, content in files.items():
        conn.execute(
            "INSERT OR REPLACE INTO codebase_files (codebase_id, path, content) VALUES (?, ?, ?)",
            (codebase_id, path, content)
        )
    conn.commit()
    conn.close()


def get_codebase_file(codebase_id: str, path: str) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT content FROM codebase_files WHERE codebase_id = ? AND path = ?",
        (codebase_id, path)
    ).fetchone()
    conn.close()
    return row["content"] if row else None


def save_codebase_context(codebase_id: str, context: dict):
    """Stores the full scanned codebase context (file tree, previews, full
    contents) as one JSON blob, keyed by codebase_id."""
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO codebase_context (codebase_id, context_json) VALUES (?, ?)",
        (codebase_id, json.dumps(context))
    )
    conn.commit()
    conn.close()


def get_codebase_context(codebase_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT context_json FROM codebase_context WHERE codebase_id = ?",
        (codebase_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["context_json"]) if row else None


def save_brd(doc_id: str, brd: dict):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO brd_store (doc_id, brd_json) VALUES (?, ?)",
        (doc_id, json.dumps(brd))
    )
    conn.commit()
    conn.close()


def get_brd(doc_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT brd_json FROM brd_store WHERE doc_id = ?",
        (doc_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["brd_json"]) if row else None