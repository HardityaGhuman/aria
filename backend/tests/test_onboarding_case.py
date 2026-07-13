"""onboarding_cases store: legal transitions (incl. the replayable dead_letter),
idempotency collision returns the existing row, audit written in the same txn, and
the DLQ is a query. Real-table, pg-gated."""
import uuid

import pytest

from backend.tests.conftest_pg import requires_pg

pytestmark = requires_pg


def _idem() -> str:
    return "onb-idem-" + uuid.uuid4().hex[:12]


@pytest.fixture()
def store():
    from backend.core import onboarding_case
    onboarding_case.initialize_onboarding_case_tables()
    return onboarding_case


def _mk(store, idem=None):
    return store.create_case("newhire@gsvh.test", "manager@gsvh.test", "backend-eng",
                             ["github", "jira", "slack", "staging-db"], idem or _idem())


def test_create_then_get(store):
    row = _mk(store)
    got = store.get_case(str(row["case_id"]))
    assert got["status"] == "draft"
    assert got["role"] == "backend-eng"
    assert got["tools"] == ["github", "jira", "slack", "staging-db"]


def test_idempotency_collision_returns_existing(store):
    key = _idem()
    a = _mk(store, key)
    b = _mk(store, key)
    assert str(a["case_id"]) == str(b["case_id"])


def test_lookup_by_idempotency_key(store):
    """The route checks for an existing Case BEFORE it extracts, so a duplicate submit
    costs neither an LLM call nor a second graph invocation."""
    key = _idem()
    row = _mk(store, key)
    found = store.get_case_by_idempotency_key(key)
    assert str(found["case_id"]) == str(row["case_id"])
    assert store.get_case_by_idempotency_key("onb-idem-nothing-here") is None


def test_legal_path_to_provisioned(store):
    cid = str(_mk(store)["case_id"])
    store.transition(cid, "pending_approval", "system", "awaiting approver")
    store.transition(cid, "approved", "manager@gsvh.test", "approved")
    row = store.transition(cid, "provisioned", "system", "granted", grant_id="grant-abc")
    assert row["status"] == "provisioned"
    assert row["grant_id"] == "grant-abc"
    events = [e["event"] for e in store.list_audit(cid)]
    assert events == ["drafted", "pending_approval", "approved", "provisioned"]


def test_illegal_transition_rejected(store):
    cid = str(_mk(store)["case_id"])
    with pytest.raises(store.OnboardingCaseError):
        store.transition(cid, "provisioned", "system", "skipping the gate")


def test_dead_letter_is_replayable_back_to_approved(store):
    cid = str(_mk(store)["case_id"])
    store.transition(cid, "pending_approval", "system", "awaiting approver")
    store.transition(cid, "approved", "manager@gsvh.test", "approved")
    row = store.transition(cid, "dead_letter", "system", "transient budget exhausted",
                           attempt=3, failure_reason="transient")
    assert row["status"] == "dead_letter"
    assert row["attempt"] == 3
    assert row["failure_reason"] == "transient"
    back = store.transition(cid, "approved", "hr@gsvh.test", "replay")
    assert back["status"] == "approved"


def test_write_failed_is_terminal(store):
    cid = str(_mk(store)["case_id"])
    store.transition(cid, "pending_approval", "system", "awaiting approver")
    store.transition(cid, "approved", "manager@gsvh.test", "approved")
    store.transition(cid, "write_failed", "system", "permanent")
    with pytest.raises(store.OnboardingCaseError):
        store.transition(cid, "approved", "hr@gsvh.test", "replay a permanent failure")


def test_list_dead_letter_is_the_queue(store):
    cid = str(_mk(store)["case_id"])
    store.transition(cid, "pending_approval", "system", "awaiting approver")
    store.transition(cid, "approved", "manager@gsvh.test", "approved")
    store.transition(cid, "dead_letter", "system", "exhausted", attempt=3, failure_reason="transient")
    assert cid in [str(r["case_id"]) for r in store.list_dead_letter()]
