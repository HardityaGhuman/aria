"""Jira agent routes: JWT-native auth, unroutable resolution, approver re-check,
kill switch. Hermetic — minimal app, monkeypatched collaborators (no network/DB/graph)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.jira_agent as jira_agent
from backend.core.auth import get_current_user


def _client(user_email="employee@gsvh.test", role="employee"):
    app = FastAPI()
    app.include_router(jira_agent.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": user_email, "role": role, "region": "us"}
    return TestClient(app)


def _enable(monkeypatch, approvers=None):
    monkeypatch.setattr(jira_agent, "JIRA_AGENT_ENABLED", True)
    monkeypatch.setattr(jira_agent, "JIRA_PROJECT_APPROVERS",
                        approvers if approvers is not None else {"MARKETING": "cmo@gsvh.test"})
    monkeypatch.setattr(jira_agent, "JIRA_ALLOWED_PROJECTS", ["MARKETING", "DESIGN"])


def test_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(jira_agent, "JIRA_AGENT_ENABLED", False)
    r = _client().post("/agents/jira", json={"text": "x"})
    assert r.status_code == 404


def test_undetermined_project_is_unroutable(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "extract_jira_fields",
                        lambda t: {"project": "", "issue_type": "Task", "summary": "s", "description": ""})
    monkeypatch.setattr(jira_agent, "create_case",
                        lambda *a, **k: {"case_id": "cid"})
    monkeypatch.setattr(jira_agent, "transition", lambda *a, **k: {"case_id": "cid", "status": "unroutable"})
    r = _client().post("/agents/jira", json={"text": "do something"})
    assert r.status_code == 200
    assert r.json()["status"] == "unroutable"


def test_unmapped_project_is_unroutable(monkeypatch):
    _enable(monkeypatch, approvers={})  # MARKETING in allowlist but no approver
    monkeypatch.setattr(jira_agent, "extract_jira_fields",
                        lambda t: {"project": "MARKETING", "issue_type": "Task", "summary": "s", "description": ""})
    monkeypatch.setattr(jira_agent, "create_case", lambda *a, **k: {"case_id": "cid"})
    monkeypatch.setattr(jira_agent, "transition", lambda *a, **k: {"case_id": "cid", "status": "unroutable"})
    r = _client().post("/agents/jira", json={"text": "marketing thing"})
    assert r.json()["status"] == "unroutable"


def test_mapped_project_starts_case(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(jira_agent, "extract_jira_fields",
                        lambda t: {"project": "MARKETING", "issue_type": "Task", "summary": "s", "description": ""})
    monkeypatch.setattr(jira_agent, "create_case", lambda *a, **k: {"case_id": "cid"})
    monkeypatch.setattr(jira_agent, "start_case",
                        lambda *a, **k: {"case_id": "cid", "status": "pending_approval", "approver_email": "cmo@gsvh.test"})
    r = _client().post("/agents/jira", json={"text": "marketing landing page"})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["approver_email"] == "cmo@gsvh.test"


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
