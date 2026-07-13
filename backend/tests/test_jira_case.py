"""jira_cases store: legal transitions, idempotency collision returns existing,
audit written in the same txn. Real-table, pg-gated. Uses unique idempotency keys
so repeat runs don't collide with rows left by an earlier run."""
import uuid

import pytest

from backend.tests.conftest_pg import requires_pg

pytestmark = requires_pg


def _idem() -> str:
    return "jira-idem-" + uuid.uuid4().hex[:12]


@pytest.fixture()
def store():
    from backend.core import jira_case
    jira_case.initialize_jira_case_tables()
    return jira_case


def _mk(store, idem=None):
    return store.create_case("employee@gsvh.test", "cmo@gsvh.test", "MARKETING",
                             "Task", "Landing page", "desc", idem or _idem())


def test_create_then_get(store):
    row = _mk(store)
    got = store.get_case(str(row["case_id"]))
    assert got["status"] == "draft"
    assert got["project"] == "MARKETING"


def test_idempotency_collision_returns_existing(store):
    key = _idem()
    a = _mk(store, key)
    b = _mk(store, key)
    assert str(a["case_id"]) == str(b["case_id"])
    audit = store.list_audit(str(a["case_id"]))
    assert sum(1 for e in audit if e["event"] == "drafted") == 1


def test_legal_transition_and_audit(store):
    row = _mk(store)
    cid = str(row["case_id"])
    store.transition(cid, "pending_approval", "system", "awaiting approver")
    store.transition(cid, "approved", "cmo@gsvh.test", "approved")
    out = store.transition(cid, "created", "system", "created", issue_key="MARKETING-1")
    assert out["status"] == "created"
    assert out["issue_key"] == "MARKETING-1"
    events = [e["event"] for e in store.list_audit(cid)]
    assert events == ["drafted", "pending_approval", "approved", "created"]


def test_illegal_transition_raises(store):
    row = _mk(store)
    with pytest.raises(store.JiraCaseError):
        store.transition(str(row["case_id"]), "created", "system", "skip approval")
