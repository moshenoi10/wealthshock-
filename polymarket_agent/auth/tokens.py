"""JWT token creation, verification, and revocation."""
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

_SECRET   = os.getenv("JWT_SECRET") or secrets.token_hex(32)
_ALGO     = "HS256"
_EXPIRY_H = 24

# In-memory revoked set (cleared on restart — acceptable for this use case)
_revoked: set = set()


def create_token(username: str, role: str) -> str:
    payload = {
        "sub":  username,
        "role": role,
        "exp":  datetime.now(timezone.utc) + timedelta(hours=_EXPIRY_H),
        "iat":  datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALGO)


def verify_token(token: str) -> Optional[dict]:
    if token in _revoked:
        return None
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGO])
    except JWTError:
        return None


def revoke_token(token: str):
    _revoked.add(token)
