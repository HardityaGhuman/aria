"""Jira agent routes: JWT-native auth, delegation to the shared filer, approver
re-check, kill switch. The FILING logic (idempotency, extract, unroutable resolution,
graph start) is owned by services/write_intake.py and tested in test_write_intake.py —
these tests only prove the route's transport: it authenticates, delegates to
write_intake.file_jira with the SERVER principal and the raw text, maps the returned
Filing to the response, and re-checks the approver on decision. Hermetic — minimal app,
monkeypatched collaborators (no network/DB/graph)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.jira_agent as jira_agent
from backend.core.auth import get_current_user
from backend.services.write_intake import Filing


def _client(user_email="employee@gsvh.test", role="employee"):
    app = FastAPI()
    app.include_router(jira_agent.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": user_email, "role": role, "region": "us"}
    return TestClient(app)


def _enable(monkeypatch):
    monkeypatch.setattr(jira_agent, "JIRA_AGENT_ENABLED", True)


def test_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(jira_agent, "JIRA_AGENT_ENABLED", False)
    r = _client().post("/agents/jira", json={"text": "x"})
    assert r.status_code == 404


def test_unroutable_filing_passes_through(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "file_jira",
                        lambda *a, **k: Filing("jira", "cid", "unroutable"))
    r = _client().post("/agents/jira", json={"text": "do something"})
    assert r.status_code == 200
    assert r.json()["status"] == "unroutable"
    assert r.json()["approver_email"] is None


def test_pending_filing_exposes_the_approver(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "file_jira",
                        lambda *a, **k: Filing("jira", "cid", "pending_approval",
                                               approver_email="cmo@gsvh.test"))
    r = _client().post("/agents/jira", json={"text": "marketing landing page"})
    body = r.json()
    assert body["case_id"] == "cid"
    assert body["status"] == "pending_approval"
    assert body["approver_email"] == "cmo@gsvh.test"


def test_route_delegates_with_the_server_principal_and_raw_text(monkeypatch):
    """Identity is the JWT-built principal, never typed; the raw text (not the model's
    output) is what the filer keys and extracts from."""
    _enable(monkeypatch)
    seen = {}

    def _fake(principal, text, **k):
        seen["email"] = principal.email
        seen["text"] = text
        return Filing("jira", "cid", "pending_approval", approver_email="cmo@gsvh.test")

    monkeypatch.setattr(jira_agent, "file_jira", _fake)
    _client(user_email="dev@gsvh.test").post("/agents/jira", json={"text": "landing page"})
    assert seen["email"] == "dev@gsvh.test"
    assert seen["text"] == "landing page"


def test_non_approver_decision_rejected(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "cmo@gsvh.test", "status": "pending_approval"})
    # caller is NOT the approver
    r = _client(user_email="intruder@gsvh.test").post("/agents/jira/cid/decision", json={"decision": "approve"})
    assert r.status_code == 403


def test_approver_decision_resumes(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "cmo@gsvh.test", "status": "pending_approval"})
    monkeypatch.setattr(jira_agent, "resume_case",
                        lambda *a, **k: {"case_id": "cid", "status": "created", "issue_key": "MARKETING-1"})
    r = _client(user_email="cmo@gsvh.test").post("/agents/jira/cid/decision", json={"decision": "approve"})
    body = r.json()
    assert body["status"] == "created"
    assert body["issue_key"] == "MARKETING-1"
