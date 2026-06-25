"""Tests for routes/admin.py — the HR-only document lifecycle. DB (doc_status)
and indexing (Chroma/embeddings) calls are monkeypatched so the tests need no
Postgres, Chroma, or embedding model."""
from datetime import datetime, timezone

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


# --- reindex ---

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


# --- RBAC: every lifecycle route is HR-only ---

def test_lifecycle_routes_forbidden_for_employee():
    h = _bearer("employee")
    assert client.get("/admin/documents", headers=h).status_code == 403
    assert client.get("/admin/documents/hr/x.md/status", headers=h).status_code == 403
    assert client.delete("/admin/documents/hr/x.md", headers=h).status_code == 403
    assert client.post(
        "/admin/documents/upload",
        headers=h,
        files={"file": ("x.md", b"hi", "text/markdown")},
        data={"department": "hr"},
    ).status_code == 403


# --- upload ---

def test_upload_saves_file_and_returns_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "DOCS_PATH", str(tmp_path))
    monkeypatch.setattr(admin, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(admin, "initialize_vectorstore", lambda: {"indexed": 1, "skipped": 0, "deleted": 0})

    resp = client.post(
        "/admin/documents/upload",
        headers=_bearer("hr"),
        files={"file": ("policy.md", b"# Policy\nbody", "text/markdown")},
        data={"department": "hr"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"document_id": "hr/policy.md", "status": "queued"}
    assert (tmp_path / "hr" / "policy.md").read_bytes() == b"# Policy\nbody"


def test_upload_rejects_unsupported_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "DOCS_PATH", str(tmp_path))
    resp = client.post(
        "/admin/documents/upload",
        headers=_bearer("hr"),
        files={"file": ("malware.exe", b"x", "application/octet-stream")},
        data={"department": "hr"},
    )
    assert resp.status_code == 400


def test_upload_rejects_traversal_department(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "DOCS_PATH", str(tmp_path))
    resp = client.post(
        "/admin/documents/upload",
        headers=_bearer("hr"),
        files={"file": ("policy.md", b"x", "text/markdown")},
        data={"department": "../../etc"},
    )
    assert resp.status_code == 400


# --- status ---

def test_status_returns_record_for_hr(monkeypatch):
    now = datetime(2026, 6, 25, tzinfo=timezone.utc)
    monkeypatch.setattr(
        admin, "get_status",
        lambda src: {"source": src, "status": "indexed", "error": None, "updated_at": now},
    )
    resp = client.get("/admin/documents/hr/employment-basics.md/status", headers=_bearer("hr"))
    assert resp.status_code == 200
    assert resp.json()["document_id"] == "hr/employment-basics.md"
    assert resp.json()["status"] == "indexed"


def test_status_404_when_untracked(monkeypatch):
    monkeypatch.setattr(admin, "get_status", lambda src: None)
    resp = client.get("/admin/documents/hr/missing.md/status", headers=_bearer("hr"))
    assert resp.status_code == 404


# --- delete ---

def test_delete_removes_file_and_chunks(tmp_path, monkeypatch):
    doc = tmp_path / "hr" / "policy.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("body")

    monkeypatch.setattr(admin, "DOCS_PATH", str(tmp_path))
    monkeypatch.setattr(admin, "delete_document_chunks", lambda rel: 5)
    monkeypatch.setattr(admin, "delete_status", lambda src: None)

    resp = client.delete("/admin/documents/hr/policy.md", headers=_bearer("hr"))
    assert resp.status_code == 200
    assert resp.json() == {"document_id": "hr/policy.md", "deleted_chunks": 5}
    assert not doc.exists()


def test_delete_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "DOCS_PATH", str(tmp_path))
    # The path-traversal guard must reject an id that escapes the docs root.
    resp = client.delete("/admin/documents/../../etc/passwd", headers=_bearer("hr"))
    assert resp.status_code in (400, 404)
