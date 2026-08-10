import os
import zipfile
import tempfile

# Extensions worth including in the summary — adjust to your stack
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb",
    ".html", ".css", ".txt", ".json", ".yaml", ".yml", ".md"
}
IGNORE_DIRS = {"node_modules", ".git", "venv", "__pycache__", "dist", "build", ".next"}


def scan_codebase(root_path: str) -> dict:
    """Walks a project folder and returns a lightweight structural summary:
    file tree + first ~30 lines of each code file, not full contents."""
    file_tree = []
    file_previews = {}
    file_full_contents = {}

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]

        for fname in filenames:
            ext = os.path.splitext(fname)[1]
            if ext not in CODE_EXTENSIONS:
                continue

            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, root_path)
            file_tree.append(rel_path)

            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    full_content = f.read()
                    file_previews[rel_path] = "\n".join(full_content.splitlines()[:30])
                    file_full_contents[rel_path] = full_content
            except Exception:
                continue

    return {
        "root_path": root_path,
        "file_tree": sorted(file_tree),
        "file_previews": file_previews,
        "file_full_contents": file_full_contents
    }


def safe_extract_zip(zip_path: str, extract_to: str):
    """Extracts a zip file safely, rejecting any entry that tries to write
    outside the intended folder (the 'zip-slip' vulnerability)."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            member_path = os.path.normpath(os.path.join(extract_to, member))
            if not member_path.startswith(os.path.normpath(extract_to) + os.sep):
                raise ValueError(f"Unsafe path in uploaded zip: {member}")
        zf.extractall(extract_to)


def extract_and_scan(zip_bytes: bytes) -> dict:
    """Takes raw zip file bytes (from an uploaded file), safely extracts
    them to a temporary folder, and returns the same summary scan_codebase
    produces for a local path."""
    tmp_dir = tempfile.mkdtemp(prefix="codebase_upload_")

    zip_path = os.path.join(tmp_dir, "upload.zip")
    with open(zip_path, "wb") as f:
        f.write(zip_bytes)

    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    safe_extract_zip(zip_path, extract_dir)

    return scan_codebase(extract_dir)