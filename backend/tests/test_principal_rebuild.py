"""Identity must never be replayed from a checkpoint.

Both write Cases sleep at ``pending_approval`` for hours/days. If the requester's
role/region changes — or the user is offboarded — while the Case sleeps, the approver's
click MUST NOT execute the write under the identity frozen at request time. The state
carries only ``user_id``; the Principal is rebuilt from the users table at every node
that needs identity, and a missing user fails the Case closed."""
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.core.hris.mock import MockHRIS
from backend.core.jira.mock import MockJira
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal, load_principal
from backend.services import jira_graph as jg
from backend.services import leave_graph as lg


class _Directory:
    """Stands in for the users table: the live source of identity truth."""

    def __init__(self, principal: Principal | None):
        self.current = principal

    def __call__(self, user_id: int) -> Principal | None:
        return self.current


class _RecordingTool:
    """Captures the Principal the write tool was actually invoked with."""

    def __init__(self, data: dict):
        self.seen: list[Principal] = []
        self._data = data

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        self.seen.append(principal)
        return ToolResult(status="ok", data=self._data)


class _FakeCaseStore:
    def __init__(self, row: dict):
        self.rows = {"cid": row}

    def transition(self, case_id, new_status, actor_id, detail, *, issue_key=None,
                   confirmation_id=None, **kw):
        # **kw: the shared write node also passes control columns (attempt, failure_reason).
        self.rows[case_id]["status"] = new_status
        if issue_key:
            self.rows[case_id]["issue_key"] = issue_key
        if confirmation_id:
            self.rows[case_id]["confirmation_id"] = confirmation_id
        return self.rows[case_id]

    def get_case(self, case_id):
        return self.rows.get(case_id)


def _p(role="employee", region="us"):
    return Principal(user_id=1, email="employee@gsvh.test", role=role, region=region)


# --- load_principal ------------------------------------------------------------------

def test_load_principal_builds_from_the_users_row(monkeypatch):
    monkeypatch.setattr(
        "backend.core.tools.principal.get_user_by_id",
        lambda uid: {"id": 7, "email": "a@gsvh.test", "role": "manager", "region": "india"},
    )
    assert load_principal(7) == Principal(user_id=7, email="a@gsvh.test", role="manager", region="india")


def test_load_principal_returns_none_when_the_user_is_gone(monkeypatch):
    monkeypatch.setattr("backend.core.tools.principal.get_user_by_id", lambda uid: None)
    assert load_principal(7) is None


# --- jira graph ----------------------------------------------------------------------

def _jira_store():
    return _FakeCaseStore({"case_id": "cid", "status": "draft", "employee_email": "employee@gsvh.test",
                           "approver_email": "cmo@gsvh.test", "issue_key": None})


def _jira_extract(raw):
    return {"project": "MARKETING", "issue_type": "Task", "summary": "Landing page", "description": "d"}


def test_jira_write_uses_the_identity_current_at_approval_not_at_request():
    store, directory = _jira_store(), _Directory(_p(region="us"))
    tool = _RecordingTool({"issue_key": "MARKETING-1", "url": "u"})
    graph = jg.build_jira_graph(jira=MockJira(projects=["MARKETING"]), checkpointer=InMemorySaver(),
                                extract_fn=_jira_extract, submit_tool=tool, case_store=store,
                                principal_loader=directory)
    jg.start_case(graph, case_id="cid", principal=_p(region="us"), raw_text="x",
                  approver_email="cmo@gsvh.test")

    directory.current = _p(region="india")  # requester region-changed while the Case slept
    jg.resume_case(graph, case_id="cid", decision="approve", actor_id="cmo@gsvh.test")

    assert tool.seen[-1].region == "india"


def test_jira_write_fails_closed_when_the_requester_no_longer_exists():
    store, directory = _jira_store(), _Directory(_p())
    tool = _RecordingTool({"issue_key": "MARKETING-1", "url": "u"})
    graph = jg.build_jira_graph(jira=MockJira(projects=["MARKETING"]), checkpointer=InMemorySaver(),
                                extract_fn=_jira_extract, submit_tool=tool, case_store=store,
                                principal_loader=directory)
    jg.start_case(graph, case_id="cid", principal=_p(), raw_text="x", approver_email="cmo@gsvh.test")

    directory.current = None  # offboarded while the Case slept
    row = jg.resume_case(graph, case_id="cid", decision="approve", actor_id="cmo@gsvh.test")

    assert row["status"] == "write_failed"
    assert tool.seen == []


# --- leave graph ---------------------------------------------------------------------

def _leave_store():
    return _FakeCaseStore({"case_id": "cid", "status": "draft", "employee_email": "employee@gsvh.test",
                           "approver_email": "manager@gsvh.test", "confirmation_id": None})


def _leave_extract(raw):
    return {"start_date": "2026-08-03", "end_date": "2026-08-05", "reason": "vacation"}


def _leave_graph(store, directory, tool):
    return lg.build_leave_graph(hris=MockHRIS(), checkpointer=InMemorySaver(),
                                extract_fn=_leave_extract, submit_tool=tool, case_store=store,
                                today=date(2026, 7, 13), principal_loader=directory)


def test_leave_write_uses_the_identity_current_at_approval_not_at_request():
    store, directory = _leave_store(), _Directory(_p(region="us"))
    # The echo matters: the write node verifies the booked dates against the approved ones.
    tool = _RecordingTool({"confirmation_id": "LV-1", "start_date": "2026-08-03",
                           "end_date": "2026-08-05"})
    graph = _leave_graph(store, directory, tool)
    lg.start_case(graph, case_id="cid", principal=_p(region="us"), raw_text="x",
                  approver_email="manager@gsvh.test")

    directory.current = _p(region="india")
    lg.resume_case(graph, case_id="cid", decision="approve", actor_id="manager@gsvh.test")

    assert tool.seen[-1].region == "india"


def test_leave_write_fails_closed_when_the_requester_no_longer_exists():
    store, directory = _leave_store(), _Directory(_p())
    # The echo matters: the write node verifies the booked dates against the approved ones.
    tool = _RecordingTool({"confirmation_id": "LV-1", "start_date": "2026-08-03",
                           "end_date": "2026-08-05"})
    graph = _leave_graph(store, directory, tool)
    lg.start_case(graph, case_id="cid", principal=_p(), raw_text="x",
                  approver_email="manager@gsvh.test")

    directory.current = None
    row = lg.resume_case(graph, case_id="cid", decision="approve", actor_id="manager@gsvh.test")

    assert row["status"] == "write_failed"
    assert tool.seen == []


def test_leave_validate_denies_when_the_requester_is_already_gone():
    store, directory = _leave_store(), _Directory(None)
    # The echo matters: the write node verifies the booked dates against the approved ones.
    tool = _RecordingTool({"confirmation_id": "LV-1", "start_date": "2026-08-03",
                           "end_date": "2026-08-05"})
    graph = _leave_graph(store, directory, tool)

    row = lg.start_case(graph, case_id="cid", principal=_p(), raw_text="x",
                        approver_email="manager@gsvh.test")

    assert row["status"] == "denied_policy"
    assert tool.seen == []


def test_states_carry_no_principal_object():
    """The checkpoint must hold a primitive user_id, never a Principal (which would be
    replayed as stale identity — and is what LangGraph's deserializer warns about)."""
    assert "principal" not in jg.JiraState.__annotations__
    assert "principal" not in lg.LeaveState.__annotations__
    assert jg.JiraState.__annotations__["user_id"] is int
    assert lg.LeaveState.__annotations__["user_id"] is int
