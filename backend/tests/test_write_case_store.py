"""The generic Case engine. The legal-transition table is DERIVED from the spec, so an
agent cannot ship a Case lifecycle that has no dead_letter — reliability is structural,
not remembered."""
import pytest

from backend.core.write.case_store import CaseSpec

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
