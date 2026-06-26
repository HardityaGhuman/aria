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
