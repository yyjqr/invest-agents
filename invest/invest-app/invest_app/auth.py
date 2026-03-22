"""JWT authentication utilities for the FastAPI server."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from invest_app.config import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"
SECRET_KEY = os.environ.get("JWT_SECRET", "invest-app-secret-change-in-prod-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days


# ---------------------------------------------------------------------------
# Simple JWT implementation (no external dependency required)
# ---------------------------------------------------------------------------

import base64
import hmac


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _create_jwt(payload: dict) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    signature = hmac.new(SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(signature)}"


def _verify_jwt(token: str) -> dict | None:
    try:
        header, body, sig = token.split(".")
        sig_input = f"{header}.{body}".encode()
        expected_sig = hmac.new(SECRET_KEY.encode(), sig_input, hashlib.sha256).digest()
        if not secrets.compare_digest(_b64url_decode(sig), expected_sig):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            return None
        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split(":", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return secrets.compare_digest(check.hex(), h)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# User store
# ---------------------------------------------------------------------------

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_users(users: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


def create_user(username: str, password: str, display_name: str = "") -> bool:
    """Create a new user. Returns False if username already exists."""
    users = _load_users()
    if username in users:
        return False
    users[username] = {
        "password": _hash_password(password),
        "display_name": display_name or username,
        "created_at": datetime.utcnow().isoformat(),
    }
    _save_users(users)
    return True


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict or None."""
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if not _verify_password(password, user["password"]):
        return None
    return {"username": username, "display_name": user.get("display_name", username)}


def create_access_token(username: str, display_name: str = "") -> str:
    payload = {
        "sub": username,
        "display_name": display_name,
        "exp": (datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp(),
    }
    return _create_jwt(payload)


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate token. Returns payload or None."""
    return _verify_jwt(token)


def ensure_default_admin() -> None:
    """Create default admin account if no users exist."""
    users = _load_users()
    if not users:
        create_user("admin", "admin123", "管理员")
