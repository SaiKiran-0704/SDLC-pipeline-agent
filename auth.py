"""
GitHub OAuth login for Foundry.

Flow:
  GET /auth/github/login    -> redirects to GitHub's consent screen
  GET /auth/github/callback -> GitHub redirects back here with a code;
                                 we exchange it for an access token, fetch
                                 the user's profile, upsert a users row,
                                 and store user_id in a signed session cookie.

Each user's own GitHub access token is stored in the users table — this is
what replaces the single shared GITHUB_PAT everywhere Deploy touches GitHub.
When a logged-in user triggers Deploy, look up their token by session
user_id instead of reading a hardcoded env var.

Scope requested is 'public_repo' (matches the public-repos-only decision
made earlier) — not 'repo', which would also grant private repo access.
"""

import os
import sqlite3
import secrets
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

GITHUB_CLIENT_ID = os.getenv("GITHUB_OAUTH_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
CALLBACK_PATH = "/auth/github/callback"

DB_PATH = "app.db"  # shares the same sqlite file the rest of the app already uses

router = APIRouter()


def init_auth_db():
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise RuntimeError(
            "GITHUB_OAUTH_CLIENT_ID / GITHUB_OAUTH_CLIENT_SECRET not set in .env — "
            "auth routes will fail without them."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            github_id INTEGER UNIQUE NOT NULL,
            username TEXT NOT NULL,
            avatar_url TEXT,
            access_token TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_current_user(request: Request) -> dict | None:
    """Call this from any route that needs to know who's logged in.
    Returns None if there's no valid session — caller decides whether
    that's an error (401) or just means 'show the logged-out view'."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


@router.get("/auth/github/login")
def github_login():
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": f"{APP_BASE_URL}{CALLBACK_PATH}",
        "scope": "public_repo",
        "state": state,
    }
    github_url = "https://github.com/login/oauth/authorize?" + urlencode(params)
    response = RedirectResponse(github_url)
    # short-lived cookie just to verify the callback isn't forged (CSRF check)
    response.set_cookie("oauth_state", state, httponly=True, max_age=600)
    return response


@router.get(CALLBACK_PATH)
def github_callback(request: Request, code: str | None = None, state: str | None = None):
    if not code:
        raise HTTPException(status_code=400, detail="GitHub did not return an authorization code")

    cookie_state = request.cookies.get("oauth_state")
    if not state or state != cookie_state:
        raise HTTPException(status_code=400, detail="OAuth state mismatch — please try logging in again")

    token_resp = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code,
            "redirect_uri": f"{APP_BASE_URL}{CALLBACK_PATH}",
        },
        timeout=10,
    )
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail=f"GitHub token exchange failed: {token_data}")

    profile_resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    profile = profile_resp.json()
    github_id = profile.get("id")
    username = profile.get("login")
    avatar_url = profile.get("avatar_url")
    if not github_id or not username:
        raise HTTPException(status_code=400, detail=f"Could not read GitHub profile: {profile}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO users (github_id, username, avatar_url, access_token)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(github_id) DO UPDATE SET
            username = excluded.username,
            avatar_url = excluded.avatar_url,
            access_token = excluded.access_token,
            updated_at = datetime('now')
    """, (github_id, username, avatar_url, access_token))
    conn.commit()
    user_row = conn.execute("SELECT id FROM users WHERE github_id = ?", (github_id,)).fetchone()
    conn.close()

    request.session["user_id"] = user_row[0]
    request.session["username"] = username

    response = RedirectResponse("/")
    response.delete_cookie("oauth_state")
    return response


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "logged out"}


@router.get("/auth/me")
def whoami(request: Request):
    user = get_current_user(request)
    if not user:
        return {"logged_in": False}
    return {
        "logged_in": True,
        "username": user["username"],
        "avatar_url": user["avatar_url"],
    }