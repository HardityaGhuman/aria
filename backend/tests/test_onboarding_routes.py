"""Onboarding routes: JWT-native identity, delegation to the shared filer, server-side
approver re-check, IDOR-checked reads, kill switch. The FILING logic (one extraction,
validator gate, unroutable/denied resolution, idempotency, graph seeding) is owned by
services/write_intake.py and tested in test_write_intake.py — these tests only prove the
route's transport: it authenticates, computes the idempotency key (client-supplied or a
raw-text hash), delegates to write_intake.file_onboarding, maps the Filing (or its
ExtractionFailed) to the response, and IDOR-checks the read. Hermetic — minimal app,
monkeypatched collaborators (no network, no DB, no graph)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.onboarding_agent as onboarding_agent
from backend.core.auth import get_current_user
from backend.services.write_intake import ExtractionFailed, Filing


def _client(user_email="newhire@gsvh.test", role="employee"):
    app = FastAPI()
    app.include_router(onboarding_agent.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": user_email, "role": role, "region": "us"}
    return TestClient(app)


def _enable(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", True)
    onboarding_agent.set_hris(object())  # file_onboarding is mocked; hris is unused here


def test_routes_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(onboarding_agent, "ONBOARDING_AGENT_ENABLED", False)
    assert _client().post("/agents/onboarding", json={"text": "x"}).status_code == 404


def test_unroutable_filing_passes_through(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "file_onboarding",
                        lambda *a, **k: Filing("onboarding", "cid", "unroutable",
                                               detail={"role": "backend-eng", "tools": []}))
    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})
    assert r.status_code == 200
    assert r.json()["status"] == "unroutable"
    assert r.json()["approver_email"] is None


def test_denied_policy_filing_passes_through_with_reason(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "file_onboarding",
                        lambda *a, **k: Filing("onboarding", "cid", "denied_policy",
                                               detail={"role": "astronaut", "tools": []},
                                               reason="astronaut is not a known role"))
    r = _client().post("/agents/onboarding", json={"text": "make me an astronaut"})
    body = r.json()
    assert body["status"] == "denied_policy"
    assert body["reason"] == "astronaut is not a known role"


def test_pending_filing_exposes_approver_role_and_tools(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(onboarding_agent, "file_onboarding",
                        lambda *a, **k: Filing("onboarding", "cid", "pending_approval",
                                               approver_email="manager@gsvh.test",
                                               detail={"role": "backend-eng",
                                                       "tools": ["github", "aws"]}))
    r = _client().post("/agents/onboarding", json={"text": "backend engineer"})
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["approver_email"] == "manager@gsvh.test"
    assert body["role"] == "backend-eng"
    assert body["tools"] == ["github", "aws"]


def test_route_delegates_with_the_server_principal_and_raw_text(monkeypatch):
    """Requester == subject: identity is the JWT principal, never typed; the raw text
    (not the model's output) is what the filer keys and extracts from."""
    _enable(monkeypatch)
    seen = {}

    def _fake(principal, text, **k):
        seen["email"] = principal.email
        seen["text"] = text
        return Filing("onboarding", "cid", "pending_approval",
                      approver_email="manager@gsvh.test",
                      detail={"role": "backend-eng", "tools": ["github"]})

    monkeypatch.setattr(onboarding_agent, "file_onboarding", _fake)
    _client(user_email="hire@gsvh.test").post("/agents/onboarding", json={"text": "backend engineer"})
    assert seen["email"] == "hire@gsvh.test"
    assert seen["text"] == "backend engineer"


def test_idempotency_key_is_keyed_off_raw_text_not_model_output(monkeypatch):
    """The model is probabilistic: keying the Case off its OUTPUT means a re-clicked
    submit that extracts differently gets a different key and forks a SECOND Case for
    the same intent. The raw text is the only deterministic thing the user gave us."""
    k1 = onboarding_agent._idempotency_key({}, "a@gsvh.test", "backend engineer, plus figma")
    k2 = onboarding_agent._idempotency_key({}, "a@gsvh.test", "backend engineer, plus figma")
    assert k1 == k2
    assert k1 != onboarding_agent._idempotency_key({}, "a@gsvh.test", "data engineer")
    assert k1 != onboarding_agent._idempotency_key({}, "b@gsvh.test", "backend engineer, plus figma")


def test_client_supplied_key_overrides_the_raw_text_hash(monkeypatch):
    """A client intent key, when present, is authoritative — two differently-worded
    submits that share one key are one intent."""
    assert onboarding_agent._idempotency_key({"idempotency_key": "abc"}, "a@x", "one wording") == "abc"
    assert onboarding_agent._idempotency_key({"idempotency_key": "abc"}, "a@x", "another wording") == "abc"


def test_unparseable_request_is_422_and_creates_no_case(monkeypatch):
    """Ref1 §8: never an unhandled exception. The filer raises ExtractionFailed when the
    model returns nothing usable — before a Case exists — and the route maps it to 422."""
    _enable(monkeypatch)

    def _boom(*a, **k):
        raise ExtractionFailed("model returned no extraction")

    monkeypatch.setattr(onboarding_agent, "file_onboarding", _boom)
    r = _client().post("/agents/onboarding", json={"text": "?????"})
    assert r.status_code == 422


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
