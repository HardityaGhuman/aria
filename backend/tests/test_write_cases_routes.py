"""The cross-agent Case surface: the requester's list, the approver's inbox, one Case with
its audit trail, and the admin's DLQ + breaker board. Hermetic — a minimal app, overridden
auth, a stubbed store: these tests are about authorization and wiring, not SQL."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI

# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.write_cases as wc
from backend.core.auth import get_current_user


class _FakeSpec:
    agent = "leave"
    result_column = "confirmation_id"


class _FakeAgent:
    name = "leave"
    spec = _FakeSpec()
    graph = None
    replay = None


def _client(email="alice@gsvh.test", role="employee"):
    app = FastAPI()
    app.include_router(wc.router)
    app.include_router(wc.admin_router)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": 1, "email": email, "role": role, "region": "us"}
    return TestClient(app)


def test_requester_sees_only_their_own_cases(monkeypatch):
    seen = {}
    monkeypatch.setattr(wc.case_store, "list_for_user",
                        lambda specs, email, role: seen.update(email=email, role=role) or [])
    r = _client().get("/agents/cases?role=requester")
    assert r.status_code == 200
    # The identity comes from the JWT, never from the query string.
    assert seen == {"email": "alice@gsvh.test", "role": "requester"}


def test_approver_inbox_is_scoped_to_the_caller(monkeypatch):
    monkeypatch.setattr(wc.case_store, "list_for_user", lambda specs, email, role: [
        {"case_id": "c1", "agent": "leave", "status": "pending_approval",
         "approver_email": email}])
    r = _client(email="boss@gsvh.test").get("/agents/cases?role=approver")
    assert [c["case_id"] for c in r.json()["cases"]] == ["c1"]


def test_an_unknown_role_is_rejected():
    assert _client().get("/agents/cases?role=admin").status_code == 422


def test_reading_one_case_requires_being_a_party_to_it(monkeypatch):
    monkeypatch.setattr(wc, "_agent_or_404", lambda name: _FakeAgent())
    monkeypatch.setattr(wc.case_store, "get_case", lambda spec, cid: {
        "case_id": cid, "status": "pending_approval", "employee_email": "alice@gsvh.test",
        "approver_email": "boss@gsvh.test"})
    monkeypatch.setattr(wc.case_store, "list_audit", lambda spec, cid: [])
    assert _client(email="alice@gsvh.test").get("/agents/cases/leave/c1").status_code == 200
    assert _client(email="boss@gsvh.test").get("/agents/cases/leave/c1").status_code == 200
    # A case_id in a URL confers no authority by itself.
    assert _client(email="eve@gsvh.test").get("/agents/cases/leave/c1").status_code == 403


def test_an_unknown_agent_is_404():
    assert _client().get("/agents/cases/notanagent/c1").status_code == 404


# --- admin surface --------------------------------------------------------------------

def test_dead_letter_queue_is_admin_only(monkeypatch):
    monkeypatch.setattr(wc.case_store, "list_dead_letter", lambda specs: [])
    assert _client(role="employee").get("/admin/write/dead-letter").status_code == 403
    assert _client(role="hr").get("/admin/write/dead-letter").status_code == 200


def test_replay_rejects_a_case_that_is_not_dead_lettered(monkeypatch):
    monkeypatch.setattr(wc, "_agent_or_404", lambda name: _FakeAgent())
    monkeypatch.setattr(wc.case_store, "get_case",
                        lambda spec, cid: {"case_id": cid, "status": "booked"})
    r = _client(role="hr").post("/admin/write/cases/leave/c1/replay")
    assert r.status_code == 409


def test_replay_resumes_a_dead_lettered_case(monkeypatch):
    called = {}

    class _Agent(_FakeAgent):
        replay = staticmethod(
            lambda graph, *, case_id, actor_id: called.update(case_id=case_id) or
            {"case_id": case_id, "status": "booked", "confirmation_id": "BK-1"})

    monkeypatch.setattr(wc, "_agent_or_404", lambda name: _Agent())
    monkeypatch.setattr(wc.case_store, "get_case",
                        lambda spec, cid: {"case_id": cid, "status": "dead_letter"})
    r = _client(role="hr").post("/admin/write/cases/leave/c1/replay")
    assert r.status_code == 200
    assert called == {"case_id": "c1"}
    assert r.json()["status"] == "booked"


def test_breaker_reset_is_explicit_and_admin_only():
    from backend.core.write.breaker import get_breaker

    breaker = get_breaker("hris")
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()
    assert _client(role="employee").post("/admin/write/breaker/hris/reset").status_code == 403
    r = _client(role="hr").post("/admin/write/breaker/hris/reset")
    assert r.status_code == 200
    assert r.json()["open"] is False
    assert get_breaker("hris").is_open() is False
