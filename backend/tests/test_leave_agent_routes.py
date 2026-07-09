"""Leave agent routes: n8n+Slack auth, approver re-check, kill switch. Hermetic —
builds a minimal app and monkeypatches the guard collaborators (no network/DB/graph)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.leave_agent as leave_agent


def _client():
    app = FastAPI()
    app.include_router(leave_agent.router)
    return TestClient(app)


N8N_HEADERS = {"Authorization": "Bearer topsecret"}


def _pass_guards(monkeypatch):
    monkeypatch.setattr(leave_agent, "LEAVE_AGENT_ENABLED", True)
    monkeypatch.setattr(leave_agent, "require_n8n_secret", lambda *a, **k: True)
    monkeypatch.setattr(leave_agent, "verify_slack_signature", lambda *a, **k: True)


def test_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(leave_agent, "LEAVE_AGENT_ENABLED", False)
    r = _client().post("/agents/leave", json={})
    assert r.status_code == 404


def test_unmapped_slack_user_refused(monkeypatch):
    _pass_guards(monkeypatch)
    monkeypatch.setattr(leave_agent, "principal_for_slack", lambda sid: None)
    r = _client().post("/agents/leave", headers=N8N_HEADERS,
                       json={"slack_user_id": "U-unknown", "text": "aug 12-14 vacation"})
    assert r.status_code == 401


def test_non_manager_click_rejected(monkeypatch):
    _pass_guards(monkeypatch)
    monkeypatch.setattr(leave_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "manager@gsvh.test", "status": "pending_approval"})
    monkeypatch.setattr(leave_agent, "principal_for_slack",
                        lambda sid: type("P", (), {"email": "intruder@gsvh.test"})())
    r = _client().post("/agents/leave/cid/decision", headers=N8N_HEADERS,
                       json={"slack_user_id": "U-intruder", "decision": "approve"})
    assert r.status_code == 403


def test_missing_n8n_secret_rejected(monkeypatch):
    monkeypatch.setattr(leave_agent, "LEAVE_AGENT_ENABLED", True)
    monkeypatch.setattr(leave_agent, "verify_slack_signature", lambda *a, **k: True)
    # N8N_SHARED_SECRET is empty in the test env, so require_n8n_secret fails closed.
    r = _client().post("/agents/leave", json={"slack_user_id": "U1", "text": "x"})
    assert r.status_code == 401
