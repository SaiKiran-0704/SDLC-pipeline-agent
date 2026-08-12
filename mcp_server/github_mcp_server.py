import os
import base64
import requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from pathlib import Path
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # format: "username/repo-name"
API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

mcp = FastMCP("github-mcp-server")


@mcp.tool()
def create_branch(branch_name: str) -> str:
    """Create a new branch off main. Use this before writing any files."""
    main_ref = requests.get(f"{API_BASE}/git/ref/heads/main", headers=HEADERS)
    main_ref.raise_for_status()
    main_sha = main_ref.json()["object"]["sha"]

    resp = requests.post(
        f"{API_BASE}/git/refs",
        headers=HEADERS,
        json={"ref": f"refs/heads/{branch_name}", "sha": main_sha},
    )
    if resp.status_code == 422:
        error_message = resp.json().get("message", "")
        if "already exists" in error_message.lower():
            return f"Branch '{branch_name}' already exists. Reusing it."
        return f"GitHub rejected this branch name: {error_message}"
    resp.raise_for_status()
    return f"Created branch '{branch_name}' from main."


@mcp.tool()
def write_file(branch_name: str, file_path: str, content: str, commit_message: str) -> str:
    """Create or update a file on the given branch with the given content."""
    existing = requests.get(
        f"{API_BASE}/contents/{file_path}",
        headers=HEADERS,
        params={"ref": branch_name},
    )
    sha = existing.json().get("sha") if existing.status_code == 200 else None

    payload = {
        "message": commit_message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": branch_name,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(f"{API_BASE}/contents/{file_path}", headers=HEADERS, json=payload)
    resp.raise_for_status()
    action = "Updated" if sha else "Created"
    return f"{action} '{file_path}' on branch '{branch_name}'."


@mcp.tool()
def open_pull_request(branch_name: str, title: str, body: str) -> str:
    """Open a pull request from the given branch back into main."""
    resp = requests.post(
        f"{API_BASE}/pulls",
        headers=HEADERS,
        json={"title": title, "body": body, "head": branch_name, "base": "main"},
    )
    resp.raise_for_status()
    pr_url = resp.json()["html_url"]
    return f"Opened pull request: {pr_url}"


if __name__ == "__main__":
    mcp.run(transport="stdio")