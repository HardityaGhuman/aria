"""The ONE write boundary, shared by all three agents. Hermetic: fake store, fake tool,
fake breaker — no DB, no LangGraph, no network. Every branch of the taxonomy is here."""
from langgraph.graph import END

from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.case_store import CaseSpec
from backend.core.write.errors import TransientWriteError
from backend.core.write.node import WriteSpec, make_write_node, make_write_router

SPEC = CaseSpec(agent="leave", table="leave_cases", audit_table="leave_case_audit",
                success_status="booked", result_column="confirmation_id",
                summary_columns=("days",))
PRINCIPAL = Principal(user_id=1, email="a@t.test", role="employee", region="us")


class _FakeStore:
    def __init__(self, status="pending_approval"):
        self.rows = {"c1": {"case_id": "c1", "status": status}}
        self.transitions = []

    def get_case(self, spec, case_id):
        return self.rows.get(case_id)

    def transition(self, spec, case_id, new_status, actor, detail, **result):
        self.transitions.append((new_status, result))
        self.rows[case_id]["status"] = new_status
        return self.rows[case_id]


def _node(invoke, *, verify=lambda r, s: True, breaker=None, loader=lambda uid: PRINCIPAL,
          store=None):
    store = store or _FakeStore()
    spec = WriteSpec(connector="hris", invoke=invoke, verify=verify,
                     success_status="booked", result_field="confirmation_id")
    return make_write_node(spec, SPEC, breaker=breaker or CircuitBreaker("hris", threshold=3),
                           principal_loader=loader, store=store), store


def _state(**over):
    base = {"case_id": "c1", "user_id": 1, "attempt": 0, "past_errors": []}
    base.update(over)
    return base


def test_success_transitions_the_row_once_and_records_the_result():
    ok = ToolResult(status="ok", data={"confirmation_id": "BK-1"}, summary="")
    node, store = _node(lambda s, p: ok)
    out = node(_state())
    assert out["status"] == "booked"
    assert out["confirmation_id"] == "BK-1"
    assert out["attempt"] == 1
    assert store.transitions[-1] == ("booked", {"confirmation_id": "BK-1", "attempt": 1})


def test_transient_failure_sets_the_class_and_writes_no_row():
    def boom(s, p):
        raise TransientWriteError("503")
    node, store = _node(boom)
    out = node(_state())
    assert out["failure_class"] == "transient"
    assert "status" not in out                      # only the router picks a terminal node
    assert [t[0] for t in store.transitions] == ["approved"]


def test_unknown_exception_is_permanent_fail_closed():
    def boom(s, p):
        raise RuntimeError("who knows")
    node, _ = _node(boom)
    assert node(_state())["failure_class"] == "permanent"


def test_open_breaker_never_touches_the_connector():
    calls = []
    breaker = CircuitBreaker("hris", threshold=1)
    breaker.record_failure()                        # open
    node, _ = _node(lambda s, p: calls.append(1), breaker=breaker)
    out = node(_state())
    assert out["failure_class"] == "breaker_open"
    assert calls == []


def test_a_clean_ok_with_a_payload_that_does_not_match_the_approval_is_permanent():
    """Execution is not correctness. A connector that answers 200 with the wrong payload
    has failed; marking it booked would write a lie into the audit log."""
    wrong = ToolResult(status="ok", data={"confirmation_id": "BK-1", "days": 9}, summary="")
    node, store = _node(lambda s, p: wrong,
                        verify=lambda r, s: r.data.get("days") == s["days"])
    out = node(_state(days=2))
    assert out["failure_class"] == "permanent"
    assert [t[0] for t in store.transitions] == ["approved"]     # never booked


def test_vanished_identity_fails_closed_without_calling_the_connector():
    calls = []
    node, _ = _node(lambda s, p: calls.append(1), loader=lambda uid: None)
    out = node(_state())
    assert out["failure_class"] == "permanent"
    assert calls == []


def test_router_is_exhaustive():
    route = make_write_router(max_attempts=3)
    assert route({"status": "booked"}) is END
    assert route({"failure_class": "permanent", "attempt": 1}) == "write_failed"
    assert route({"failure_class": "breaker_open", "attempt": 1}) == "dead_letter"
    assert route({"failure_class": "transient", "attempt": 3}) == "dead_letter"
    assert route({"failure_class": "transient", "attempt": 1}) == "write"    # the retry EDGE
