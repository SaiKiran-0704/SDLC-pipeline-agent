import sqlite3
import os

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