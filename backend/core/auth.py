"""
core/auth.py
------------
Authentication primitives: password hashing (bcrypt), JWT access tokens
(PyJWT, HS256), and FastAPI dependencies that gate endpoints by identity and
role. The single seam to swap for asymmetric keys / a managed IdP at the
serverless step.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
# pyrefly: ignore [missing-import]
import jwt
# pyrefly: ignore [missing-import]
from fastapi import Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.core.config import (
    ACCESS_TOKEN_TTL_MIN,
    JWT_ALGORITHM,
    REFRESH_TOKEN_TTL_DAYS,
    require_jwt_secret,
)


@dataclass
class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or tampered."""
    message: str


# --- RBAC ---
# The access tiers a role may retrieve. HR sees everything; everyone else sees
# only "all"-tier documents. Generic by design: a restricted tier is hidden from
# non-HR roles without naming any specific document.
_ROLE_TIERS = {
    "hr": ["all", "manager", "hr_only"],
    "manager": ["all", "manager"],
}
_DEFAULT_TIERS = ["all"]


def tiers_for_role(role: str | None) -> list[str]:
    """Return the access tiers visible to ``role`` (defaults to all-only)."""
    return _ROLE_TIERS.get(role, _DEFAULT_TIERS)


# --- Region filtering ---
# global docs are visible to every region; region-specific docs are visible
# only to users whose home region matches. Applies to all roles including HR.
_DEFAULT_REGION = "us"


def regions_for_user(region: str | None) -> list[str]:
    """Doc regions a user may retrieve: global is universal, plus their home region."""
    return ["global", region or _DEFAULT_REGION]


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt (random per-password salt)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user: dict, expires_in_min: int | None = None) -> str:
    """Sign a short-lived HS256 access JWT (id/role/region, ``type:"access"``)."""
    minutes = ACCESS_TOKEN_TTL_MIN if expires_in_min is None else expires_in_min
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "region": user.get("region", _DEFAULT_REGION),
        "email": user.get("email"),
        "type": "access",
        "jti": uuid.uuid4().hex,
        "exp": now + timedelta(minutes=minutes),
        "iat": now,
    }
    return jwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user: dict) -> tuple[str, str, datetime]:
    """Sign a long-lived refresh JWT. Returns ``(token, jti, expires_at)`` so the
    caller can persist the jti for rotation/revocation."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user["id"]),
        "type": "refresh",
        "jti": jti,
        "exp": expires_at,
        "iat": now,
    }
    return jwt.encode(payload, require_jwt_secret(), algorithm=JWT_ALGORITHM), jti, expires_at


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
        # Only access tokens authenticate requests; a refresh token presented as
        # a Bearer credential must be rejected.
        if claims.get("type") != "access":
            raise AuthError("Not an access token")
        user = {
            "id": int(claims["sub"]),
            "role": claims["role"],
            "region": claims.get("region", _DEFAULT_REGION),
            "email": claims.get("email"),
        }
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
