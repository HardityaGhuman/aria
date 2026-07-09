"""LeaveCase store: projection + append-only audit + enforced state machine.
Live-pg only (@requires_pg). Uses unique idempotency keys so runs don't collide."""
import uuid

from backend.tests.conftest_pg import requires_pg


def _key():
    return "idem-" + uuid.uuid4().hex[:12]


def _lc():
    import backend.core.leave_case as lc
    lc.initialize_leave_case_tables()
    return lc


@requires_pg
def test_create_is_idempotent_by_key():
    lc = _lc()
    key = _key()
    a = lc.create_case("employee@gsvh.test", "manager@gsvh.test", "2026-08-12", "2026-08-14", 3, "vacation", key)
    b = lc.create_case("employee@gsvh.test", "manager@gsvh.test", "2026-08-12", "2026-08-14", 3, "vacation", key)
    assert a["case_id"] == b["case_id"]
    assert a["status"] == "draft"
    events = [e["event"] for e in lc.list_audit(a["case_id"])]
    assert events.count("drafted") == 1


@requires_pg
def test_legal_transition_appends_audit():
    lc = _lc()
    case = lc.create_case("employee@gsvh.test", "manager@gsvh.test", "2026-08-12", "2026-08-14", 3, "vacation", _key())
    updated = lc.transition(case["case_id"], "pending_approval", actor_id="system", detail="awaiting manager")
    assert updated["status"] == "pending_approval"
    assert "pending_approval" in [e["event"] for e in lc.list_audit(case["case_id"])]


@requires_pg
def test_illegal_transition_rejected():
    lc = _lc()
    case = lc.create_case("employee@gsvh.test", "manager@gsvh.test", "2026-08-12", "2026-08-14", 3, "vacation", _key())
    import pytest
    with pytest.raises(lc.LeaveCaseError):
        lc.transition(case["case_id"], "booked", actor_id="system", detail="skip approval")


@requires_pg
def test_book_sets_confirmation():
    lc = _lc()
    case = lc.create_case("employee@gsvh.test", "manager@gsvh.test", "2026-08-12", "2026-08-14", 3, "vacation", _key())
    lc.transition(case["case_id"], "pending_approval", "system", "await")
    lc.transition(case["case_id"], "approved", "manager@gsvh.test", "approved")
    booked = lc.transition(case["case_id"], "booked", "system", "booked", confirmation_id="BK-XYZ")
    assert booked["confirmation_id"] == "BK-XYZ"
