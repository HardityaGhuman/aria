"""Every write request runs inside a trace context (audit gap 1).

Before this, `start_trace()` was called only on the read path (chat_service), so the
graph's `case_*` events and the `extract` LLM span both emitted `trace_id: null` — the
one LLM call inside a Case could not be joined to that Case, or to anything else.

The write routes now open a trace for the whole request and reset it after, so
everything emitted underneath (graph nodes, write attempts, LLM spans) shares one id.
Hermetic: minimal app, overridden auth, stubbed graph — no DB, no network.
"""
import json
import logging

# pyrefly: ignore [missing-import]
import pytest
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.jira_agent as jira_agent
import backend.routes.leave_agent as leave_agent
import backend.routes.onboarding_agent as onboarding_agent
from backend.core.auth import get_current_user
from backend.core.trace import current_trace
from backend.core.write.trace import case_node_started


def _app(module, user_role="employee"):
    app = FastAPI()
    app.include_router(module.router)
    if hasattr(module, "admin_router"):
        app.include_router(module.admin_router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 7, "email": "newhire@gsvh.test", "role": user_role, "region": "us"}
    return TestClient(app)


def _telemetry(caplog):
    return [json.loads(r.message) for r in caplog.records if r.name == "telemetry"]


class _FakeHRIS:
    def manager_email(self, principal):
        return "manager@gsvh.test"


@pytest.fixture
def seen():
    return {}


def _stub_onboarding(monkeypatch, seen):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    onboarding_agent.set_hris(_FakeHRIS())
    monkeypatch.setattr(onboarding_agent, "extract_onboarding_fields",
                        lambda t: {"role": "backend-eng", "extra_tools": []})
    monkeypatch.setattr(onboarding_agent, "get_case_by_idempotency_key", lambda k: None)
    monkeypatch.setattr(onboarding_agent, "create_case",
                        lambda *a, **k: {"case_id": "cid", "status": "draft"})

    def _start(*_a, **_k):
        # Stands in for the graph: whatever a node emits mid-request must carry the id.
        seen["trace"] = current_trace()
        case_node_started("cid", "validate")
        return {"case_id": "cid", "status": "pending_approval"}

    monkeypatch.setattr(onboarding_agent, "start_case", _start)


def test_onboarding_start_runs_inside_a_trace_and_events_carry_the_id(monkeypatch, seen, caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    _stub_onboarding(monkeypatch, seen)

    r = _app(onboarding_agent).post("/agents/onboarding", json={"text": "backend engineer"})

    assert r.status_code == 200
    assert seen["trace"] is not None, "write route did not open a trace context"
    emitted = [e for e in _telemetry(caplog) if e["event"] == "case_node_started"]
    assert emitted and emitted[0]["trace_id"] == seen["trace"].trace_id
    assert seen["trace"].user_id == 7


def test_trace_is_reset_after_the_request(monkeypatch, seen):
    _stub_onboarding(monkeypatch, seen)
    _app(onboarding_agent).post("/agents/onboarding", json={"text": "backend engineer"})
    # A leaked ContextVar would let the NEXT request inherit a stale trace_id.
    assert current_trace() is None


def test_onboarding_decision_runs_inside_a_trace(monkeypatch, seen):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    monkeypatch.setattr(onboarding_agent, "get_case", lambda cid: {
        "case_id": cid, "status": "pending_approval", "approver_email": "newhire@gsvh.test"})

    def _resume(*_a, **_k):
        seen["trace"] = current_trace()
        return {"case_id": "cid", "status": "provisioned"}

    monkeypatch.setattr(onboarding_agent, "resume_case", _resume)
    r = _app(onboarding_agent).post("/agents/onboarding/cid/decision", json={"decision": "approve"})
    assert r.status_code == 200
    assert seen["trace"] is not None


def test_admin_replay_runs_inside_a_trace(monkeypatch, seen):
    """The replay lives on the agent-agnostic /admin/write surface now, but the property is
    the same: the work the replay drives must emit under a trace id."""
    import backend.routes.write_cases as write_cases

    def _replay(_graph, *, case_id, actor_id):
        seen["trace"] = current_trace()
        return {"case_id": case_id, "status": "provisioned", "grant_id": "g1"}

    class _Agent:
        name = "onboarding"
        spec = type("S", (), {"result_column": "grant_id"})()
        graph = None
        replay = staticmethod(_replay)

    monkeypatch.setattr(write_cases, "_agent_or_404", lambda name: _Agent())
    monkeypatch.setattr(write_cases.case_store, "get_case",
                        lambda spec, cid: {"case_id": cid, "status": "dead_letter"})
    r = _app(write_cases, user_role="hr").post("/admin/write/cases/onboarding/cid/replay")
    assert r.status_code == 200
    assert seen["trace"] is not None


def test_jira_start_runs_inside_a_trace(monkeypatch, seen):
    monkeypatch.setattr(jira_agent, "JIRA_AGENT_ENABLED", True)
    monkeypatch.setattr(jira_agent, "extract_jira_fields", lambda t: {
        "project": "MARKETING", "issue_type": "Task", "summary": "s", "description": "d"})
    monkeypatch.setattr(jira_agent, "JIRA_PROJECT_APPROVERS", {"MARKETING": "manager@gsvh.test"})
    monkeypatch.setattr(jira_agent, "create_case", lambda *a, **k: {"case_id": "cid"})

    def _start(*_a, **_k):
        seen["trace"] = current_trace()
        return {"case_id": "cid", "status": "pending_approval"}

    monkeypatch.setattr(jira_agent, "start_case", _start)
    r = _app(jira_agent).post("/agents/jira", json={"text": "make a task"})
    assert r.status_code == 200
    assert seen["trace"] is not None


def test_leave_start_runs_inside_a_trace(monkeypatch, seen):
    """Leave is the Slack/n8n edge — no JWT — so its trace opens with user_id=None.
    The id still has to exist: without it the Case's LLM span is orphaned all the same."""
    from backend.core.tools.principal import Principal

    monkeypatch.setattr(leave_agent, "LEAVE_AGENT_ENABLED", True)
    monkeypatch.setattr(leave_agent, "require_n8n_secret", lambda *a, **k: True)
    monkeypatch.setattr(leave_agent, "verify_slack_signature", lambda *a, **k: True)
    monkeypatch.setattr(leave_agent, "principal_for_slack",
                        lambda sid: Principal(user_id=7, email="a@gsvh.test", role="employee", region="us"))
    monkeypatch.setattr(leave_agent, "extract_leave_fields", lambda t: {
        "start_date": "2026-08-01", "end_date": "2026-08-02", "reason": "trip"})
    monkeypatch.setattr(leave_agent, "compute_days", lambda a, b: 2)
    monkeypatch.setattr(leave_agent, "create_case", lambda *a, **k: {"case_id": "cid"})
    monkeypatch.setattr(leave_agent, "slack_user_for_email", lambda e: "U1")
    monkeypatch.setattr(leave_agent, "_HRIS", _FakeHRIS())

    def _start(*_a, **_k):
        seen["trace"] = current_trace()
        return {"case_id": "cid", "status": "pending_approval"}

    monkeypatch.setattr(leave_agent, "start_case", _start)
    r = _app(leave_agent).post("/agents/leave",
                               json={"slack_user_id": "U1", "text": "2 days off"})
    assert r.status_code == 200
    assert seen["trace"] is not None
