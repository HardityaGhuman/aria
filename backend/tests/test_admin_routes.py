"""Tests for routes/admin.py — reindex is HR-only. The actual indexing call is
monkeypatched so the test needs no Chroma/embeddings."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

from backend.core import auth
import backend.routes.admin as admin

app = FastAPI()
app.include_router(admin.router)
client = TestClient(app)


def _bearer(role: str) -> dict:
    return {"Authorization": f"Bearer {auth.create_access_token({'id': 1, 'role': role})}"}


def test_reindex_forbidden_for_employee():
    assert client.post("/admin/reindex", headers=_bearer("employee")).status_code == 403


def test_reindex_requires_auth():
    assert client.post("/admin/reindex").status_code == 401


def test_reindex_returns_stats_for_hr(monkeypatch):
    monkeypatch.setattr(
        admin, "initialize_vectorstore", lambda: {"indexed": 3, "skipped": 1, "deleted": 0}
    )
    resp = client.post("/admin/reindex", headers=_bearer("hr"))
    assert resp.status_code == 200
    assert resp.json() == {"indexed": 3, "skipped": 1, "deleted": 0}
