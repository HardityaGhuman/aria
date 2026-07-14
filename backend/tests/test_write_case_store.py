"""The generic Case engine. The legal-transition table is DERIVED from the spec, so an
agent cannot ship a Case lifecycle that has no dead_letter — reliability is structural,
not remembered."""
import uuid

import pytest

from backend.core.write import case_store
from backend.core.write.case_store import CaseSpec
from backend.tests.conftest_pg import requires_pg

pg = requires_pg

LEAVE = CaseSpec(agent="leave", table="leave_cases", audit_table="leave_case_audit",
                 success_status="booked", result_column="confirmation_id",
                 summary_columns=("start_date", "end_date", "days"))


def test_transitions_are_derived_from_the_success_status():
    t = LEAVE.legal_transitions()
    assert t["draft"] == {"pending_approval", "denied_policy", "unroutable"}
    assert t["pending_approval"] == {"approved", "denied_manager"}
    assert t["approved"] == {"booked", "write_failed", "dead_letter"}


def test_dead_letter_is_replayable_and_the_rest_are_terminal():
    t = LEAVE.legal_transitions()
    assert t["dead_letter"] == {"approved"}
    for terminal in ("booked", "write_failed", "denied_policy", "denied_manager", "unroutable"):
        assert t[terminal] == set()


def test_every_agent_gets_the_same_failure_states():
    jira = CaseSpec(agent="jira", table="jira_cases", audit_table="jira_case_audit",
                    success_status="created", result_column="issue_key",
                    summary_columns=("project", "summary"))
    assert {"write_failed", "dead_letter"} <= jira.legal_transitions()["approved"]


def test_a_success_status_that_collides_with_a_control_status_is_rejected():
    with pytest.raises(ValueError):
        CaseSpec(agent="x", table="x", audit_table="x_audit", success_status="approved",
                 result_column="r", summary_columns=())


def _idem() -> str:
    return "wc-" + uuid.uuid4().hex[:12]


@pg
def test_create_get_and_idempotency_collision():
    from backend.core import leave_case
    leave_case.initialize_leave_case_tables()
    key = _idem()
    a = case_store.create_case(LEAVE, "e@t.test", "m@t.test", key,
                               start_date="2026-08-01", end_date="2026-08-02", days=1,
                               reason="trip")
    b = case_store.create_case(LEAVE, "e@t.test", "m@t.test", key,
                               start_date="2026-08-01", end_date="2026-08-02", days=1,
                               reason="trip")
    assert str(a["case_id"]) == str(b["case_id"])          # no second Case
    got = case_store.get_case(LEAVE, str(a["case_id"]))
    assert got["status"] == "draft"
    assert str(case_store.get_by_idempotency_key(LEAVE, key)["case_id"]) == str(a["case_id"])
    assert case_store.get_by_idempotency_key(LEAVE, "wc-nothing") is None
    # exactly one audit row: the draft
    assert [r["event"] for r in case_store.list_audit(LEAVE, str(a["case_id"]))] == ["drafted"]


@pg
def test_illegal_transition_is_rejected_and_writes_no_audit_row():
    from backend.core import leave_case
    leave_case.initialize_leave_case_tables()
    row = case_store.create_case(LEAVE, "e@t.test", "m@t.test", _idem(),
                                 start_date="2026-08-01", end_date="2026-08-02", days=1,
                                 reason="trip")
    cid = str(row["case_id"])
    with pytest.raises(case_store.WriteCaseError):
        case_store.transition(LEAVE, cid, "booked", "system", "skipping the gate")
    assert case_store.get_case(LEAVE, cid)["status"] == "draft"
    assert [r["event"] for r in case_store.list_audit(LEAVE, cid)] == ["drafted"]


@pg
def test_full_legal_path_records_the_result_and_the_audit_trail():
    from backend.core import leave_case
    leave_case.initialize_leave_case_tables()
    row = case_store.create_case(LEAVE, "e@t.test", "m@t.test", _idem(),
                                 start_date="2026-08-01", end_date="2026-08-02", days=1,
                                 reason="trip")
    cid = str(row["case_id"])
    case_store.transition(LEAVE, cid, "pending_approval", "system", "awaiting manager")
    case_store.transition(LEAVE, cid, "approved", "m@t.test", "approved")
    done = case_store.transition(LEAVE, cid, "booked", "system", "booked",
                                 confirmation_id="BK-1", attempt=1)
    assert done["confirmation_id"] == "BK-1"
    assert done["attempt"] == 1
    assert [r["event"] for r in case_store.list_audit(LEAVE, cid)] == [
        "drafted", "pending_approval", "approved", "booked"]


@pg
def test_dead_letter_is_replayable_back_to_approved():
    from backend.core import leave_case
    leave_case.initialize_leave_case_tables()
    row = case_store.create_case(LEAVE, "e@t.test", "m@t.test", _idem(),
                                 start_date="2026-08-01", end_date="2026-08-02", days=1,
                                 reason="trip")
    cid = str(row["case_id"])
    case_store.transition(LEAVE, cid, "pending_approval", "system", "awaiting manager")
    case_store.transition(LEAVE, cid, "approved", "m@t.test", "approved")
    case_store.transition(LEAVE, cid, "dead_letter", "system", "connector down",
                          attempt=3, failure_reason="transient")
    back = case_store.transition(LEAVE, cid, "approved", "hr@t.test", "replay")
    assert back["status"] == "approved"


@pg
def test_jira_migration_drops_risk_tier_and_renames_the_denied_statuses():
    """risk_tier was never read or written — a column nothing reads is a lie about the
    system. And jira spoke a private dialect (denied_validation/denied_approver) that no
    cross-agent DLQ or UI could group on."""
    from backend.core import db, jira_case
    jira_case.initialize_jira_case_tables()
    with db.pooled(lambda: AssertionError("no pg")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'jira_cases'")
            cols = {r[0] for r in cur.fetchall()}
    assert "risk_tier" not in cols
    assert {"attempt", "failure_reason"} <= cols
    assert "dead_letter" in jira_case.JIRA_SPEC.statuses()
    assert jira_case.JIRA_SPEC.legal_transitions()["draft"] == {
        "pending_approval", "denied_policy", "unroutable"}


@pg
def test_leave_gains_the_reliability_columns_and_dead_letter():
    from backend.core import db, leave_case
    leave_case.initialize_leave_case_tables()
    with db.pooled(lambda: AssertionError("no pg")) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'leave_cases'")
            cols = {r[0] for r in cur.fetchall()}
    assert {"attempt", "failure_reason"} <= cols
    assert "dead_letter" in leave_case.LEAVE_SPEC.statuses()


@pg
def test_initialize_is_re_runnable():
    """Startup DDL runs on every boot. If it is not idempotent, the second boot dies."""
    from backend.core import jira_case, leave_case, onboarding_case
    for _ in range(2):
        leave_case.initialize_leave_case_tables()
        jira_case.initialize_jira_case_tables()
        onboarding_case.initialize_onboarding_case_tables()


@pg
def test_dead_letter_queue_spans_every_agent():
    """The DLQ is a query, not a table — and it must be ONE query, or an admin has to
    remember to check three pages to learn that automation is stuck."""
    from backend.core import jira_case, leave_case
    leave_case.initialize_leave_case_tables()
    jira_case.initialize_jira_case_tables()

    lc = case_store.create_case(leave_case.LEAVE_SPEC, "e@t.test", "m@t.test", _idem(),
                                start_date="2026-08-01", end_date="2026-08-02", days=1,
                                reason="trip")
    jc = case_store.create_case(jira_case.JIRA_SPEC, "e@t.test", "m@t.test", _idem(),
                                project="MARKETING", issue_type="Task", summary="s",
                                description="d")
    for spec, row in ((leave_case.LEAVE_SPEC, lc), (jira_case.JIRA_SPEC, jc)):
        cid = str(row["case_id"])
        case_store.transition(spec, cid, "pending_approval", "system", "gate")
        case_store.transition(spec, cid, "approved", "m@t.test", "approved")
        case_store.transition(spec, cid, "dead_letter", "system", "connector down",
                              attempt=3, failure_reason="transient")

    dlq = case_store.list_dead_letter([leave_case.LEAVE_SPEC, jira_case.JIRA_SPEC])
    agents = {r["agent"] for r in dlq}
    assert {"leave", "jira"} <= agents
    assert all(r["status"] == "dead_letter" for r in dlq)


@pg
def test_list_for_user_separates_my_requests_from_my_approvals():
    """The manager's inbox. Without this query the HITL gate is reachable only by someone who
    already knows the Case's UUID — which is to say, by nobody."""
    from backend.core import leave_case
    leave_case.initialize_leave_case_tables()
    specs = [leave_case.LEAVE_SPEC]
    row = case_store.create_case(leave_case.LEAVE_SPEC, "alice@t.test", "boss@t.test", _idem(),
                                 start_date="2026-08-01", end_date="2026-08-02", days=1,
                                 reason="trip")
    cid = str(row["case_id"])
    case_store.transition(leave_case.LEAVE_SPEC, cid, "pending_approval", "system", "gate")

    mine = case_store.list_for_user(specs, "alice@t.test", "requester")
    assert cid in {str(r["case_id"]) for r in mine}

    inbox = case_store.list_for_user(specs, "boss@t.test", "approver")
    assert cid in {str(r["case_id"]) for r in inbox}
    assert all(r["status"] == "pending_approval" for r in inbox)   # only what still needs me

    # A stranger sees neither — the query IS the authorization boundary.
    assert case_store.list_for_user(specs, "eve@t.test", "requester") == []
    assert case_store.list_for_user(specs, "eve@t.test", "approver") == []
