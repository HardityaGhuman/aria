"""Regression tests: the chat data-plane enforces session ownership (IDOR).

Before this fix, GET/DELETE /chat/history/{id} had no ownership check and POST
/chat / /chat/stream accepted any session_id, so an authenticated user could
read or wipe another user's history, or inject into their session and read it
back via a meta question. These lock that down.
"""
# pyrefly: ignore [missing-import]
import pytest
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient
# pyrefly: ignore [missing-import]
from slowapi.errors import RateLimitExceeded

import backend.routes.chat as chatroute
from backend.core.auth import get_current_user
from backend.core.ratelimit import limiter, rate_limit_handler


class _FakeStore:
    """Single session "owned-by-1" belonging to user 1."""

    def owner_of(self, session_id):
        return 1 if session_id == "owned-by-1" else None


_current = {"user": {"id": 1, "role": "employee", "region": "us"}}


def _as(uid: int) -> None:
    _current["user"] = {"id": uid, "role": "employee", "region": "us"}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(chatroute, "session_store", _FakeStore())
    # Owner happy-path must not touch the real DB.
    monkeypatch.setattr(chatroute, "get_session_history", lambda sid: [])
    monkeypatch.setattr(chatroute, "clear_session_history", lambda sid: None)
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.include_router(chatroute.router, prefix="/chat")
    app.dependency_overrides[get_current_user] = lambda: _current["user"]
    _as(1)
    return TestClient(app)


def test_get_history_cross_user_forbidden(client):
    _as(2)
    assert client.get("/chat/history/owned-by-1").status_code == 403


def test_delete_history_cross_user_forbidden(client):
    _as(2)
    assert client.delete("/chat/history/owned-by-1").status_code == 403


def test_get_history_owner_allowed(client):
    _as(1)
    assert client.get("/chat/history/owned-by-1").status_code == 200


def test_get_history_unknown_session_is_404(client):
    _as(1)
    assert client.get("/chat/history/does-not-exist").status_code == 404


def test_chat_send_into_others_session_forbidden(client, monkeypatch):
    # If the gate failed open, the service would run — make that an explicit failure.
    async def _must_not_run(*args, **kwargs):
        raise AssertionError("generate_chat_reply reached on a cross-user session")

    monkeypatch.setattr(chatroute, "generate_chat_reply", _must_not_run)
    _as(2)
    resp = client.post("/chat", json={"message": "recap our chat", "session_id": "owned-by-1"})
    assert resp.status_code == 403
