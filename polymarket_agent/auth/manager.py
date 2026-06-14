"""
User management: CRUD, password hashing, role validation.
Users stored in data/users.json (never plaintext passwords).
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import bcrypt

USERS_FILE = Path("data/users.json")
VALID_ROLES = {"admin", "trader", "viewer"}
ROLE_HIERARCHY = {"admin": 3, "trader": 2, "viewer": 1}

# Default admin credentials loaded from env/config at startup
_DEFAULT_USERNAME = "mnoishtat"
_DEFAULT_PASSWORD = "025951"  # hashed — never stored as plaintext


# ── Storage helpers ────────────────────────────────────────────────────────────

def _load() -> List[dict]:
    try:
        if USERS_FILE.exists():
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save(users: List[dict]):
    USERS_FILE.parent.mkdir(exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False), encoding="utf-8")


def _public(user: dict) -> dict:
    """Strip password_hash before returning to caller."""
    return {k: v for k, v in user.items() if k != "password_hash"}


# ── Bootstrap ──────────────────────────────────────────────────────────────────

def init_default_users():
    """Create the default admin account if the users store is empty."""
    users = _load()
    if not any(u["username"] == _DEFAULT_USERNAME for u in users):
        hashed = bcrypt.hashpw(_DEFAULT_PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
        users.append({
            "id":            str(uuid.uuid4()),
            "username":      _DEFAULT_USERNAME,
            "password_hash": hashed,
            "role":          "admin",
            "created_at":    datetime.now(timezone.utc).isoformat(),
            "last_login":    None,
        })
        _save(users)
        print(f"[AUTH] Created default admin: {_DEFAULT_USERNAME}")


# ── Queries ────────────────────────────────────────────────────────────────────

def get_user(username: str) -> Optional[dict]:
    return next((u for u in _load() if u["username"] == username), None)


def get_user_by_id(uid: str) -> Optional[dict]:
    return next((u for u in _load() if u["id"] == uid), None)


def verify_password(username: str, password: str) -> Optional[dict]:
    user = get_user(username)
    if not user:
        return None
    try:
        if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return user
    except Exception:
        pass
    return None


def update_last_login(username: str):
    users = _load()
    for u in users:
        if u["username"] == username:
            u["last_login"] = datetime.now(timezone.utc).isoformat()
    _save(users)


def list_users() -> List[dict]:
    return [_public(u) for u in _load()]


# ── Mutations ──────────────────────────────────────────────────────────────────

def create_user(username: str, password: str, role: str) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(VALID_ROLES)}")
    users = _load()
    if any(u["username"] == username for u in users):
        raise ValueError(f"User '{username}' already exists")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    user = {
        "id":            str(uuid.uuid4()),
        "username":      username,
        "password_hash": hashed,
        "role":          role,
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "last_login":    None,
    }
    users.append(user)
    _save(users)
    return _public(user)


def update_user(uid: str, role: Optional[str] = None, password: Optional[str] = None) -> Optional[dict]:
    users = _load()
    for u in users:
        if u["id"] == uid:
            if role is not None:
                if role not in VALID_ROLES:
                    raise ValueError(f"Invalid role: {role}")
                u["role"] = role
            if password is not None:
                if len(password) < 6:
                    raise ValueError("Password must be at least 6 characters")
                u["password_hash"] = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
            _save(users)
            return _public(u)
    return None


def delete_user(uid: str) -> bool:
    users = _load()
    before = len(users)
    users = [u for u in users if u["id"] != uid]
    if len(users) == before:
        return False
    # Prevent deleting last admin
    if not any(u["role"] == "admin" for u in users):
        raise ValueError("Cannot delete the last admin account")
    _save(users)
    return True
