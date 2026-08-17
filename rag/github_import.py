"""
Import a codebase directly from a public GitHub repo URL, as an alternative
to uploading a zip. Produces the same context shape as codebase_context.extract_and_scan
(file_tree, file_previews, file_full_contents) so it plugs into the existing pipeline
with zero changes downstream.

Uses GitHub's unauthenticated REST API, which is rate-limited to 60 requests/hour
per IP. That's enough for occasional imports of small-to-medium repos, but will
fail on very large repos or heavy testing. If this becomes a real bottleneck,
the fix is to pass a GITHUB_PAT through as a Bearer token to raise the limit to
5,000/hour — deliberately not done here yet since it would silently let this
endpoint spend from the same token budget as Deploy.
"""

import re
import base64
import requests

GITHUB_API = "https://api.github.com"

# Safety limits — mirrors the spirit of the zip-upload safety checks
MAX_FILES = 150
MAX_FILE_BYTES = 200_000       # skip individual files bigger than this
MAX_TOTAL_BYTES = 5_000_000    # stop importing once this much content is collected

SKIP_DIR_PREFIXES = (
    ".git/", "node_modules/", "venv/", ".venv/", "__pycache__/",
    "dist/", "build/", ".next/", "target/",
)
SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".pdf",
    ".pyc", ".so", ".dylib", ".dll", ".exe",
    ".lock",
)


class GitHubImportError(Exception):
    pass


def _parse_repo_url(repo_url: str):
    """Accepts 'https://github.com/owner/repo', 'github.com/owner/repo',
    or plain 'owner/repo' and returns (owner, repo)."""
    repo_url = repo_url.strip().rstrip("/")
    repo_url = re.sub(r"\.git$", "", repo_url)
    match = re.search(r"(?:github\.com/)?([^/\s]+)/([^/\s]+)$", repo_url)
    if not match:
        raise GitHubImportError(f"Couldn't parse a GitHub owner/repo from: {repo_url}")
    return match.group(1), match.group(2)


def _detect_default_branch(owner: str, repo: str, token: str | None = None) -> str:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers, timeout=10)
    if resp.status_code == 404:
        raise GitHubImportError(f"Repo '{owner}/{repo}' not found — check the URL, or the repo may be private.")
    if resp.status_code == 403:
        raise GitHubImportError("GitHub API rate limit hit. Try again later, or make sure you're logged in.")
    resp.raise_for_status()
    return resp.json().get("default_branch", "main")


def _should_skip(path: str, size: int) -> bool:
    lower = path.lower()
    if any(lower.startswith(p) or f"/{p}" in lower for p in SKIP_DIR_PREFIXES):
        return True
    if lower.endswith(SKIP_EXTENSIONS):
        return True
    if size > MAX_FILE_BYTES:
        return True
    return False


def fetch_github_codebase(repo_url: str, branch: str | None = None, token: str | None = None) -> dict:
    owner, repo = _parse_repo_url(repo_url)
    branch = branch or _detect_default_branch(owner, repo, token)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    tree_resp = requests.get(
        f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}",
        params={"recursive": "1"},
        headers=headers,
        timeout=15,
    )
    if tree_resp.status_code == 404:
        raise GitHubImportError(f"Branch '{branch}' not found on '{owner}/{repo}'.")
    if tree_resp.status_code == 403:
        raise GitHubImportError("GitHub API rate limit hit. Try again later, or make sure you're logged in.")
    tree_resp.raise_for_status()
    tree = tree_resp.json().get("tree", [])

    candidates = [
        item for item in tree
        if item.get("type") == "blob" and not _should_skip(item["path"], item.get("size", 0))
    ]
    if not candidates:
        raise GitHubImportError("No importable text files found in this repo (after skipping binaries, lockfiles, and build output).")
    if len(candidates) > MAX_FILES:
        candidates = candidates[:MAX_FILES]

    file_tree = []
    file_previews = {}
    file_full_contents = {}
    total_bytes = 0

    for item in candidates:
        if total_bytes >= MAX_TOTAL_BYTES:
            break
        path = item["path"]
        blob_resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs/{item['sha']}",
            headers=headers,
            timeout=10,
        )
        if blob_resp.status_code != 200:
            continue
        blob = blob_resp.json()
        if blob.get("encoding") != "base64":
            continue
        try:
            content = base64.b64decode(blob["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue  # binary file that slipped past the extension filter

        total_bytes += len(content)
        file_tree.append(path)
        file_previews[path] = content[:1000]
        file_full_contents[path] = content

    if not file_tree:
        raise GitHubImportError("Files were found but none could be read as text — this repo may be entirely binary or too large.")

    return {
        "root_path": f"github:{owner}/{repo}@{branch}",
        "file_tree": file_tree,
        "file_previews": file_previews,
        "file_full_contents": file_full_contents,
    }
