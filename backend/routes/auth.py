"""
routes/auth.py
--------------
Authentication endpoints: login, token refresh, logout, and /auth/me.

Token model: a short-lived **access** JWT is returned in the JSON body (the SPA
holds it in memory) and a long-lived **refresh** JWT is set in an HttpOnly,
SameSite=Strict cookie scoped to /auth/refresh. Refresh tokens rotate on every
use (the old jti is revoked) and are revoked on logout. ``issue_tokens`` is the
single mint path a future OAuth/OIDC provider reuses unchanged.
"""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field

from backend.core import tokens, users
from backend.core.auth import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.core.config import (
    COOKIE_SECURE,
    FRONTEND_ORIGIN,
    RATE_LIMIT_LOGIN,
    REFRESH_TOKEN_TTL_DAYS,
)
from backend.core.logging import get_logger
from backend.core.ratelimit import limiter

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")

REFRESH_COOKIE = "refresh_token"
REFRESH_PATH = "/auth/refresh"


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    # Capped at 72 bytes: bcrypt silently ignores anything beyond that.
    password: str = Field(min_length=1, max_length=72)


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path=REFRESH_PATH,
        max_age=REFRESH_TOKEN_TTL_DAYS * 86400,
    )


def issue_tokens(user: dict, response: Response) -> str:
    """Mint an access token + a rotating refresh token, persist the refresh jti,
    set the refresh cookie, and return the access token. The single seam every
    identity provider (password now, OIDC later) funnels through."""
    access = create_access_token(user)
    refresh, jti, expires_at = create_refresh_token(user)
    tokens.store_refresh(jti, int(user["id"]), expires_at)
    _set_refresh_cookie(response, refresh)
    return access


def _check_origin(request: Request) -> None:
    """CSRF guard for the refresh endpoint: reject a cross-site Origin/Referer.
    SameSite=Strict is the primary defense; this is defense-in-depth."""
    origin = request.headers.get("origin") or request.headers.get("referer", "")
    if origin and not origin.startswith(FRONTEND_ORIGIN):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Bad origin")


@router.post("/login")
@limiter.limit(RATE_LIMIT_LOGIN)
def login(request: Request, body: LoginRequest, response: Response):
    try:
        user = users.get_user_by_email(body.email)
    except users.UserError:
        logger.exception("Login failed: users store unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    if user is None:
        verify_password(body.password, _DUMMY_HASH)  # equalize timing
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    access = issue_tokens(user, response)
    return {"access_token": access, "token_type": "bearer", "role": user["role"], "region": user.get("region", "us")}


@router.post("/refresh")
def refresh(request: Request, response: Response):
    """Rotate: validate the refresh cookie, revoke its jti, issue a fresh pair."""
    _check_origin(request)
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")
    try:
        claims = decode_token(raw)
    except AuthError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if claims.get("type") != "refresh" or not claims.get("jti"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if not tokens.is_valid(claims["jti"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked or expired")

    tokens.revoke(claims["jti"])  # rotation: old refresh can never be reused
    user = users.get_user_by_id(int(claims["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User no longer exists")

    access = issue_tokens(user, response)
    return {"access_token": access, "token_type": "bearer", "role": user["role"], "region": user.get("region", "us")}


@router.post("/logout")
def logout(request: Request, response: Response):
    """Revoke the current refresh token and clear the cookie."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        try:
            claims = decode_token(raw)
            if claims.get("jti"):
                tokens.revoke(claims["jti"])
        except AuthError:
            pass
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
    return {"message": "logged out"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user
