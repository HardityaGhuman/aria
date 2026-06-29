"""
core/ratelimit.py
-----------------
Per-user / per-IP rate limiting at the API edge (slowapi). Protects the backend
and the LLM token budget from a single client hammering the chat or login
endpoints.

Key strategy: authenticated callers are limited per **user id** (the token's
``sub`` claim), so one user's spike doesn't throttle everyone behind a shared
NAT; anonymous callers fall back to client IP. A breach renders the uniform
error envelope with ``Retry-After``.

Why ``sub`` and not the raw token: every ``/auth/refresh`` mints a fresh access
token (new ``jti``), so keying on the token string handed a caller a brand-new
bucket per rotation — refresh-then-spam bypassed the chat limit entirely. The
``sub`` is stable across rotations, so the bucket follows the user.
"""
# pyrefly: ignore [missing-import]
from fastapi import Request
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse
# pyrefly: ignore [missing-import]
from slowapi import Limiter
# pyrefly: ignore [missing-import]
from slowapi.errors import RateLimitExceeded
# pyrefly: ignore [missing-import]
from slowapi.util import get_remote_address

from backend.core.auth import AuthError, decode_token


def _rate_key(request: Request) -> str:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            sub = decode_token(token).get("sub")
        except AuthError:
            sub = None  # garbage/expired token → fall through to IP keying
        if sub:
            return f"user:{sub}"
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_key)


async def rate_limit_handler(_request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Render a 429 in the uniform error envelope, with Retry-After."""
    response = JSONResponse(
        status_code=429,
        content={"error": {
            "code": "rate_limited",
            "message": "Too many requests. Please slow down and try again shortly.",
            "detail": str(exc.detail) if getattr(exc, "detail", None) else None,
        }},
    )
    response.headers["Retry-After"] = "60"
    return response
