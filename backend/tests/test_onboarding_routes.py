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
    monkeypatch.setattr(onboarding_agent, "create_case", lambda *a, **k: {"case_id": "cid"})


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


# --- admin routes -----------------------------------------------------------------

def _admin_client(role="hr", email="hr@gsvh.test"):
    app = FastAPI()
    app.include_router(onboarding_agent.admin_router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 9, "email": email, "role": role, "region": "us"}
    return TestClient(app)


def test_replay_is_admin_only(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    r = _admin_client(role="employee", email="newhire@gsvh.test").post(
        "/admin/onboarding/cases/cid/replay")
    assert r.status_code == 403


def test_replay_rejects_a_case_that_is_not_dead_lettered(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "status": "provisioned"})
    r = _admin_client().post("/admin/onboarding/cases/cid/replay")
    assert r.status_code == 409


def test_replay_resumes_a_dead_lettered_case(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    monkeypatch.setattr(onboarding_agent, "get_case",
                        lambda cid: {"case_id": cid, "status": "dead_letter"})
    monkeypatch.setattr(onboarding_agent, "replay_case",
                        lambda *a, **k: {"case_id": "cid", "status": "provisioned", "grant_id": "g1"})
    r = _admin_client().post("/admin/onboarding/cases/cid/replay")
    assert r.status_code == 200
    assert r.json()["status"] == "provisioned"


def test_dead_letter_queue_is_listable(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    monkeypatch.setattr(onboarding_agent, "list_dead_letter",
                        lambda: [{"case_id": "cid", "status": "dead_letter", "attempt": 3}])
    r = _admin_client().get("/admin/onboarding/dead-letter")
    assert r.json()["cases"][0]["case_id"] == "cid"


def test_breaker_reset_is_explicit_and_admin_only(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    from backend.core.write.breaker import get_breaker
    breaker = get_breaker("access-provisioner")
    for _ in range(breaker.threshold):
        breaker.record_failure()
    assert breaker.is_open() is True

    assert _admin_client(role="employee").post("/admin/onboarding/breaker/reset").status_code == 403

    r = _admin_client().post("/admin/onboarding/breaker/reset")
    assert r.status_code == 200
    assert r.json()["open"] is False
    assert get_breaker("access-provisioner").is_open() is False
