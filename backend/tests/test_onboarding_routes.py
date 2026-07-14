"""Onboarding routes: JWT-native identity, unroutable (no manager) before the gate,
server-side approver re-check, IDOR-checked reads, kill switch. Hermetic — minimal
app, monkeypatched collaborators (no network, no DB, no graph)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.onboarding_agent as onboarding_agent
from backend.core.auth import get_current_user


def _client(user_email="newhire@gsvh.test", role="employee"):
    app = FastAPI()
    app.include_router(onboarding_agent.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": user_email, "role": role, "region": "us"}
    return TestClient(app)


class _FakeHRIS:
    def __init__(self, manager="manager@gsvh.test"):
        self._manager = manager

    def manager_email(self, principal):
        return self._manager


def _enable(monkeypatch, manager="manager@gsvh.test"):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    onboarding_agent.set_hris(_FakeHRIS(manager))
    monkeypatch.setattr(onboarding_agent, "extract_onboarding_fields",
                        lambda t: {"role": "backend-eng", "extra_tools": []})
    monkeypatch.setattr(onboarding_agent, "get_case_by_idempotency_key", lambda key: None)
    monkeypatch.setattr(onboarding_agent, "create_case",
                        lambda *a, **k: {"case_id": "cid", "status": "draft"})


def test_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", False)
    assert _client().post("/agents/onboarding", json={"text": "x"}).status_code == 404


def test_no_manager_is_unroutable_before_the_gate(monkeypatch):
    _enable(monkeypatch, manager=None)
    monkeypatch.setattr(onboarding_agent, "transition",
                        lambda *a, **k: {"case_id": "cid", "status": "unroutable"})
    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})
    assert r.status_code == 200
    assert r.json()["status"] == "unroutable"


def test_off_catalog_request_is_denied_policy_without_starting_the_graph(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "extract_onboarding_fields",
                        lambda t: {"role": "astronaut", "extra_tools": []})
    monkeypatch.setattr(onboarding_agent, "transition",
                        lambda *a, **k: {"case_id": "cid", "status": "denied_policy"})
    r = _client().post("/agents/onboarding", json={"text": "make me an astronaut"})
    assert r.json()["status"] == "denied_policy"


def test_valid_request_starts_the_case(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "start_case",
                        lambda *a, **k: {"case_id": "cid", "status": "pending_approval"})
    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["approver_email"] == "manager@gsvh.test"


def test_route_seeds_its_extraction_into_the_graph(monkeypatch):
    """One extraction per Case: the graph must receive the SAME role/tools that were
    written on the Case row, or the manager approves one bundle and the connector
    grants another."""
    _enable(monkeypatch)
    seen = {}
    monkeypatch.setattr(onboarding_agent, "start_case",
                        lambda *a, **k: seen.update(k) or {"case_id": "cid", "status": "pending_approval"})
    _client().post("/agents/onboarding", json={"text": "backend engineer"})
    assert seen["role"] == "backend-eng"
    assert seen["extra_tools"] == []


def test_idempotency_key_is_keyed_off_raw_text_not_model_output(monkeypatch):
    """The model is probabilistic: keying the Case off its OUTPUT means a re-clicked
    submit that extracts differently gets a different key and forks a SECOND Case for
    the same intent. The raw text is the only deterministic thing the user gave us."""
    k1 = onboarding_agent._idempotency_key({}, "a@gsvh.test", "backend engineer, plus figma")
    k2 = onboarding_agent._idempotency_key({}, "a@gsvh.test", "backend engineer, plus figma")
    assert k1 == k2
    assert k1 != onboarding_agent._idempotency_key({}, "a@gsvh.test", "data engineer")
    assert k1 != onboarding_agent._idempotency_key({}, "b@gsvh.test", "backend engineer, plus figma")


def test_duplicate_submit_returns_the_existing_case_without_extracting_or_invoking_the_graph(monkeypatch):
    """Ref1 §4 'fake resume': the old route re-invoked the graph from START on a thread
    that was already interrupted at the approval gate — re-running nodes and appending
    checkpoints. A Case that already exists is READ, never re-driven."""
    _enable(monkeypatch)
    calls = {"extract": 0, "graph": 0, "create": 0}
    monkeypatch.setattr(onboarding_agent, "extract_onboarding_fields",
                        lambda t: calls.update(extract=calls["extract"] + 1) or
                        {"role": "backend-eng", "extra_tools": []})
    monkeypatch.setattr(onboarding_agent, "create_case",
                        lambda *a, **k: calls.update(create=calls["create"] + 1) or
                        {"case_id": "cid", "status": "draft"})
    monkeypatch.setattr(onboarding_agent, "start_case",
                        lambda *a, **k: calls.update(graph=calls["graph"] + 1) or
                        {"case_id": "cid", "status": "pending_approval"})
    monkeypatch.setattr(onboarding_agent, "get_case_by_idempotency_key", lambda key: {
        "case_id": "cid", "status": "pending_approval", "role": "backend-eng",
        "tools": ["github", "aws"], "approver_email": "manager@gsvh.test"})

    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})

    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == "cid" and body["status"] == "pending_approval"
    assert body["tools"] == ["github", "aws"]
    assert calls == {"extract": 0, "graph": 0, "create": 0}


def test_a_racing_duplicate_that_slips_past_the_lookup_still_never_re_enters_the_graph(monkeypatch):
    """Two concurrent submits both miss the pre-check; the UNIQUE key makes create_case
    return the existing non-draft row. The status guard is the second line of defence."""
    _enable(monkeypatch)
    started = []
    monkeypatch.setattr(onboarding_agent, "create_case", lambda *a, **k: {
        "case_id": "cid", "status": "pending_approval", "role": "backend-eng",
        "tools": ["github"], "approver_email": "manager@gsvh.test"})
    monkeypatch.setattr(onboarding_agent, "start_case",
                        lambda *a, **k: started.append(k) or {"case_id": "cid", "status": "x"})
    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})
    assert r.json()["status"] == "pending_approval"
    assert started == []


def test_unparseable_request_is_422_and_creates_no_case(monkeypatch):
    """Ref1 §8: never an unhandled exception. The model returned nothing usable, so the
    request ends before a Case exists — no half-built Case to clean up."""
    _enable(monkeypatch)

    def _boom(text):
        raise onboarding_agent.OnboardingExtractError("model returned no extraction")

    monkeypatch.setattr(onboarding_agent, "extract_onboarding_fields", _boom)
    created = []
    monkeypatch.setattr(onboarding_agent, "create_case",
                        lambda *a, **k: created.append(a) or {"case_id": "cid"})
    r = _client().post("/agents/onboarding", json={"text": "?????"})
    assert r.status_code == 422
    assert created == []


def test_non_approver_decision_rejected(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "manager@gsvh.test",
                                     "employee_email": "newhire@gsvh.test", "status": "pending_approval"})
    r = _client(user_email="intruder@gsvh.test").post(
        "/agents/onboarding/cid/decision", json={"decision": "approve"})
    assert r.status_code == 403


def test_approver_decision_resumes(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "manager@gsvh.test",
                                     "employee_email": "newhire@gsvh.test", "status": "pending_approval"})
    monkeypatch.setattr(onboarding_agent, "resume_case",
                        lambda *a, **k: {"case_id": "cid", "status": "provisioned", "grant_id": "grant-1"})
    r = _client(user_email="manager@gsvh.test").post(
        "/agents/onboarding/cid/decision", json={"decision": "approve"})
    body = r.json()
    assert body["status"] == "provisioned"
    assert body["grant_id"] == "grant-1"


def test_stranger_cannot_read_a_case(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "manager@gsvh.test",
                                     "employee_email": "newhire@gsvh.test", "status": "provisioned",
                                     "role": "backend-eng", "tools": ["github"], "grant_id": "g"})
    monkeypatch.setattr(onboarding_agent, "list_audit", lambda cid: [])
    assert _client(user_email="stranger@gsvh.test").get("/agents/onboarding/cid").status_code == 403


def test_owner_can_read_their_case(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "approver_email": "manager@gsvh.test",
                                     "employee_email": "newhire@gsvh.test", "status": "provisioned",
                                     "role": "backend-eng", "tools": ["github"], "grant_id": "g"})
    monkeypatch.setattr(onboarding_agent, "list_audit", lambda cid: [{"event": "drafted"}])
    r = _client(user_email="newhire@gsvh.test").get("/agents/onboarding/cid")
    assert r.status_code == 200
    assert r.json()["status"] == "provisioned"
    assert r.json()["audit"] == [{"event": "drafted"}]

# The admin routes (DLQ, replay, breaker reset) moved to the agent-agnostic
# /admin/write surface once leave and jira could dead-letter too — their coverage now
# lives in test_write_cases_routes.py.
