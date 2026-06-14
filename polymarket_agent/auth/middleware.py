"""FastAPI dependencies for authentication and role-based access control."""
from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from auth.tokens import verify_token

ROLE_HIERARCHY = {"admin": 3, "trader": 2, "viewer": 1}


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    # Cookie fallback (for browser sessions)
    return request.cookies.get("auth_token")


def get_current_user(request: Request) -> dict:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalid or expired")
    return {"username": payload["sub"], "role": payload["role"], "token": token}


def require_role(minimum: str = "viewer") -> Callable:
    """Return a FastAPI dependency that enforces a minimum role level."""
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_HIERARCHY.get(user["role"], 0) < ROLE_HIERARCHY.get(minimum, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {minimum} (you have: {user['role']})",
            )
        return user
    return dep


def require_admin() -> Callable:
    return require_role("admin")
