"""Tests for user-owned sessions (Task 4).

Route ownership/isolation is tested against a fake in-memory SessionStore (no
DB). A separate DB-gated test exercises the real PostgresSessionStore CRUD.
"""
import pytest
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.chat as chatroute
from backend.core.auth import get_current_user


class _FakeStore:
    def __init__(self):
        self._rows: dict[str, dict] = {}
        self._n = 0

    def create(self, owner_user_id, title=None):
        self._n += 1
        sid = f"sess-{self._n}"
        self._rows[sid] = {"owner": owner_user_id, "title": title, "updated_at": None}
        return sid

    def list_for_owner(self, owner_user_id):
        return [
            {"session_id": sid, "title": r["title"], "updated_at": r["updated_at"]}
            for sid, r in self._rows.items()
            if r["owner"] == owner_user_id
        ]

    def owner_of(self, session_id):
        row = self._rows.get(session_id)
        return row["owner"] if row else None

    def rename(self, session_id, title):
        self._rows[session_id]["title"] = title

    def delete(self, session_id):
        self._rows.pop(session_id, None)


# Shared mutable identity so we can flip the "logged-in" user between requests.
_current = {"user": {"id": 1, "role": "employee", "region": "us"}}


def _make_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(chatroute, "session_store", _FakeStore())
    app = FastAPI()
    app.include_router(chatroute.router, prefix="/chat")
    app.dependency_overrides[get_current_user] = lambda: _current["user"]
    return TestClient(app)


def _as(user_id: int):
    _current["user"] = {"id": user_id, "role": "employee", "region": "us"}


def test_owner_isolation_across_users(monkeypatch):
    client = _make_client(monkeypatch)

    _as(1)
    sid = client.post("/chat/sessions").json()["session_id"]
    assert [s["session_id"] for s in client.get("/chat/sessions").json()] == [sid]

    _as(2)
    assert client.get("/chat/sessions").json() == []
    assert client.patch(f"/chat/sessions/{sid}", json={"title": "hijack"}).status_code == 403
    assert client.delete(f"/chat/sessions/{sid}").status_code == 403

    _as(1)
    renamed = client.patch(f"/chat/sessions/{sid}", json={"title": "My chat"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "My chat"
    assert client.delete(f"/chat/sessions/{sid}").status_code == 200


def test_unknown_session_is_404(monkeypatch):
    client = _make_client(monkeypatch)
    _as(1)
    assert client.delete("/chat/sessions/does-not-exist").status_code == 404


# --- PostgresSessionStore round-trip (needs Postgres; skipped if unavailable) ---

def _store_or_skip():
    from backend.core.chat_memory import ChatMemoryError, initialize_chat_memory
    from backend.core.session_store import PostgresSessionStore
    try:
        initialize_chat_memory()
    except ChatMemoryError:
        pytest.skip("Postgres not available for session-store tests")
    return PostgresSessionStore()


def test_postgres_session_store_crud():
    store = _store_or_skip()
    sid = store.create(4242, title="t1")
    assert store.owner_of(sid) == 4242
    assert any(r["session_id"] == sid for r in store.list_for_owner(4242))
    store.rename(sid, "t2")
    assert next(r for r in store.list_for_owner(4242) if r["session_id"] == sid)["title"] == "t2"
    store.delete(sid)
    assert store.owner_of(sid) is None
