"""Tests for rate limiting (spec §11): exceeding the limit returns 429 +
Retry-After in the uniform error envelope. Uses the real limiter + handler on a
throwaway route limited to 1/minute so a single extra request trips it."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
from slowapi.errors import RateLimitExceeded

from backend.core.ratelimit import limiter, rate_limit_handler


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    @app.get("/ping")
    @limiter.limit("1/minute")
    async def ping(request: Request):
        return {"ok": True}

    return app


def test_rate_limit_returns_429_uniform_envelope():
    client = TestClient(_make_app(), raise_server_exceptions=False)

    first = client.get("/ping")
    assert first.status_code == 200

    second = client.get("/ping")  # same key (IP), over the 1/minute limit
    assert second.status_code == 429
    assert second.headers.get("Retry-After")
    body = second.json()
    assert body["error"]["code"] == "rate_limited"
    assert isinstance(body["error"]["message"], str)


# --- key strategy: bucket must follow the user, not the (rotating) token ---

import os

os.environ.setdefault("JWT_SECRET", "test-secret-for-ratelimit")

from backend.core.auth import create_access_token  # noqa: E402
from backend.core.ratelimit import _rate_key  # noqa: E402


class _FakeRequest:
    def __init__(self, headers=None, client_host="1.2.3.4"):
        self.headers = headers or {}

        class _Client:
            host = client_host

        self.client = _Client()


def test_same_user_different_tokens_share_one_key():
    # The bug: /auth/refresh mints a new token each call; token-keyed limiting
    # handed out a fresh bucket per rotation. Two tokens, same sub -> one key.
    user = {"id": 42, "role": "employee", "region": "us", "email": "a@b.c"}
    t1 = create_access_token(user)
    t2 = create_access_token(user)
    assert t1 != t2
    k1 = _rate_key(_FakeRequest({"authorization": f"Bearer {t1}"}))
    k2 = _rate_key(_FakeRequest({"authorization": f"Bearer {t2}"}))
    assert k1 == k2 == "user:42"


def test_different_users_get_different_keys():
    a = create_access_token({"id": 1, "role": "employee", "region": "us"})
    b = create_access_token({"id": 2, "role": "employee", "region": "us"})
    ka = _rate_key(_FakeRequest({"authorization": f"Bearer {a}"}))
    kb = _rate_key(_FakeRequest({"authorization": f"Bearer {b}"}))
    assert ka != kb


def test_garbage_token_falls_back_to_ip():
    key = _rate_key(_FakeRequest({"authorization": "Bearer not-a-jwt"}, client_host="9.9.9.9"))
    assert key == "9.9.9.9"


def test_no_auth_header_uses_ip():
    assert _rate_key(_FakeRequest({}, client_host="5.6.7.8")) == "5.6.7.8"
