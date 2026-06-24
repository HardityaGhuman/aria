"""
core/auth.py
------------
Authentication primitives: password hashing (bcrypt), JWT access tokens
(PyJWT, HS256), and FastAPI dependencies that gate endpoints by identity and
role. The single seam to swap for asymmetric keys / a managed IdP at the
serverless step.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
# pyrefly: ignore [missing-import]
import jwt
# pyrefly: ignore [missing-import]
from fastapi import Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.config import JWT_ALGORITHM, JWT_EXPIRY_HOURS, require_jwt_secret


@dataclass
class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or tampered."""
    message: str


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt (random per-password salt)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user: dict, expires_in_hours: int | None = None) -> str:
    """Sign an HS256 JWT carrying the user's id (``sub``), role, and expiry."""
    hours = JWT_EXPIRY_HOURS if expires_in_hours is None else expires_in_hours
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "exp": now + timedelta(hours=hours),
        "iat": now,
    }
    return jwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Verify signature + expiry and return the claims, or raise ``AuthError``."""
    try:
        return jwt.decode(token, require_jwt_secret(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError(f"Invalid or expired token: {exc}") from exc


# --- FastAPI dependencies ---
# auto_error=False so a missing/garbage header yields our own clean 401 rather
# than FastAPI's default 403.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Resolve the caller's identity from the Bearer token claims (id + role).

    Stateless by design: no DB hit per request, which suits the serverless
    direction. Tradeoff — a deleted or role-changed user keeps access until the
    token expires (JWT_EXPIRY_HOURS).
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(credentials.credentials)
        user = {"id": int(claims["sub"]), "role": claims["role"]}
    except (AuthError, KeyError, ValueError, TypeError) as exc:
        # AuthError = bad signature/expiry; the rest = a token whose payload is
        # missing/malformed sub/role. All are "not authenticated", never a 500.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return user


def require_role(role: str):
    """Dependency factory: 403 unless the current user has ``role``."""

    def _checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _checker
