"""services/leave_graph.py
------------------------
The leave write Case as a LangGraph. This is the ONE place LangGraph earns its keep:
the Case sleeps at ``request_approval`` (an ``interrupt``) for minutes/hours awaiting a
human click, must survive a process restart (Postgres checkpointer), and must resume
at the EXACT interrupted node via ``Command(resume=...)`` on the same thread_id — not
re-run from START. Reads stay single-step elsewhere; no LangGraph there.

Every collaborator (HRIS, extract fn, case store, submit tool, today) is injected so
the graph is fully unit-testable with an InMemorySaver and stubs. The LLM appears in
exactly one node (``extract``); every routing/approval/write decision is deterministic
Python. Control fields in the state are set by nodes, never by the model.

``build_leave_graph`` returns a ``LeaveGraph`` wrapper bundling the compiled graph with
the case-store it was built against, so ``start_case``/``resume_case`` read status from
the SAME store the nodes wrote (the store is the source of truth for status — on an
interrupt the graph state has not yet returned the node's status field)."""
from dataclasses import dataclass
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.core import leave_case as leave_case_store
from backend.core.config import WRITE_MAX_ATTEMPTS
from backend.core.hris import HRISClient
from backend.core.hris.mock import MockHRIS
from backend.core.leave_case import LEAVE_SPEC
from backend.core.tools.principal import load_principal
from backend.core.tools.submit_leave import SubmitLeaveTool
from backend.core.write.breaker import get_breaker
from backend.core.write.node import (
    WriteSpec,
    adapt_store,
    make_terminal_nodes,
    make_write_node,
    make_write_router,
)
from backend.services.leave_extract import extract_leave_fields
from backend.services.leave_validator import validate_leave

_IDENTITY_GONE = "requester no longer exists"
CONNECTOR = "hris"


class LeaveState(TypedDict, total=False):
    case_id: str
    user_id: int                  # identity ANCHOR only — the Principal is reloaded per node
    raw_text: str                 # the only LLM input
    approver_email: str | None
    start_date: str
    end_date: str
    days: int
    reason: str
    validation_ok: bool
    validation_reason: str | None
    decision: str | None          # set by resume: "approve" | "deny"
    decision_actor: str | None
    confirmation_id: str | None
    # --- execution memory: the control layer's inputs. Set by nodes, never by the LLM.
    attempt: int
    past_errors: list[str]
    failure_class: str | None
    status: str


@dataclass
class LeaveGraph:
    compiled: object
    case_store: object
    breaker: object = None


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": f"case-{case_id}"}}


def build_leave_graph(*, hris: HRISClient | None = None, checkpointer, extract_fn=None,
                      submit_tool=None, case_store=leave_case_store, today: date | None = None,
                      principal_loader=None, breaker=None,
                      max_attempts: int = WRITE_MAX_ATTEMPTS) -> LeaveGraph:
    hris = hris or MockHRIS()
    extract_fn = extract_fn or extract_leave_fields
    submit_tool = submit_tool or SubmitLeaveTool(hris)
    principal_loader = principal_loader or load_principal
    breaker = breaker or get_breaker(CONNECTOR)

    def extract(state: LeaveState) -> dict:
        fields = extract_fn(state["raw_text"])
        return {"start_date": fields["start_date"], "end_date": fields["end_date"],
                "reason": fields["reason"]}

    def validate(state: LeaveState) -> dict:
        principal = principal_loader(state["user_id"])
        if principal is None:
            return {"validation_ok": False, "validation_reason": _IDENTITY_GONE, "days": 0}
        r = validate_leave(principal, hris, state["start_date"], state["end_date"], today=today)
        return {"validation_ok": r.ok, "validation_reason": r.reason, "days": r.days}

    def deny_policy(state: LeaveState) -> dict:
        case_store.transition(state["case_id"], "denied_policy", "system",
                              state.get("validation_reason") or "validation failed")
        return {"status": "denied_policy"}

    def request_approval(state: LeaveState) -> dict:
        # Pre-interrupt work MUST be idempotent: on resume this node re-runs from here.
        # Guard the transition by the current status so a resume doesn't re-transition.
        current = case_store.get_case(state["case_id"])
        if current and current["status"] == "draft":
            case_store.transition(state["case_id"], "pending_approval", "system", "awaiting manager")
        payload = interrupt({
            "case_id": state["case_id"], "days": state["days"],
            "start_date": state["start_date"], "end_date": state["end_date"],
            "approver_email": state.get("approver_email"),
        })
        return {"decision": payload["decision"], "decision_actor": payload.get("actor_id")}

    def deny_manager(state: LeaveState) -> dict:
        case_store.transition(state["case_id"], "denied_manager",
                              state.get("decision_actor") or "manager", "denied by manager")
        return {"status": "denied_manager"}

    # The write boundary is THE shared one (`core/write/node.py`). Leave supplies only what
    # is agent-specific: which connector, how to call it, and what "the write the human
    # approved" means. Before this, `book` made ONE call and any error at all — a timeout,
    # a 503 — ended an approved request at write_failed with no retry and no way back.
    engine = adapt_store(case_store)
    write_spec = WriteSpec(
        connector=CONNECTOR,
        invoke=lambda state, principal: submit_tool.invoke(
            {"case_id": state["case_id"], "start_date": state["start_date"],
             "end_date": state["end_date"], "days": state["days"]}, principal),
        # The HRIS answering is not the same as the HRIS booking what was approved: a
        # confirmation for the WRONG dates is a failure wearing a success's clothes.
        verify=lambda result, state: bool((result.data or {}).get("confirmation_id"))
        and (result.data or {}).get("start_date") == state["start_date"]
        and (result.data or {}).get("end_date") == state["end_date"],
        success_status="booked",
        result_field="confirmation_id",
    )
    write = make_write_node(write_spec, LEAVE_SPEC, breaker=breaker,
                            principal_loader=principal_loader, store=engine)
    after_write = make_write_router(max_attempts)
    write_failed, dead_letter = make_terminal_nodes(LEAVE_SPEC, store=engine)

    # --- routers (pure functions, exhaustive) ---
    def after_validate(state: LeaveState) -> str:
        return "request_approval" if state["validation_ok"] else "deny_policy"

    def after_approval(state: LeaveState) -> str:
        return "write" if state.get("decision") == "approve" else "deny_manager"

    g = StateGraph(LeaveState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("deny_policy", deny_policy)
    g.add_node("request_approval", request_approval)
    g.add_node("write", write)
    g.add_node("deny_manager", deny_manager)
    g.add_node("write_failed", write_failed)
    g.add_node("dead_letter", dead_letter)

    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["request_approval", "deny_policy"])
    g.add_edge("deny_policy", END)
    g.add_conditional_edges("request_approval", after_approval, ["write", "deny_manager"])
    g.add_conditional_edges("write", after_write,
                            ["write", "write_failed", "dead_letter", END])
    g.add_edge("write_failed", END)
    g.add_edge("dead_letter", END)
    g.add_edge("deny_manager", END)
    return LeaveGraph(compiled=g.compile(checkpointer=checkpointer), case_store=case_store,
                      breaker=breaker)


def start_case(graph: LeaveGraph, *, case_id, principal, raw_text, approver_email, case_store=None):
    """Run the graph to its interrupt (pending_approval) or a terminal denial."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        {"case_id": case_id, "user_id": principal.user_id, "raw_text": raw_text,
         "approver_email": approver_email, "attempt": 0, "past_errors": []},
        thread_config(case_id),
    )
    return store.get_case(case_id)


def resume_case(graph: LeaveGraph, *, case_id, decision, actor_id, case_store=None):
    """Resume the paused Case with the manager's decision; run to terminal status."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        Command(resume={"decision": decision, "actor_id": actor_id}),
        thread_config(case_id),
    )
    return store.get_case(case_id)


def replay_case(graph: LeaveGraph, *, case_id, actor_id, case_store=None):
    """Replay a dead-lettered Case from its checkpoint. The DLQ is a queue, not a
    graveyard: the connector recovered, so re-enter the write on the SAME thread — no
    re-extraction, no second approval, no forked audit log. Idempotency by case_id means a
    replay that races a late success cannot double-book.

    The replay CLEARS the connector's breaker first. A Case that exhausts its transient
    budget usually opens the breaker on its way out (N consecutive failures IS the
    threshold), so a replay against an open breaker would short-circuit straight back to
    dead_letter and the DLQ could never be drained. Clearing it here is not self-healing:
    a human explicitly asserted the connector is back, which is exactly the "explicit
    clearance" the breaker demands — and if they are wrong, the next failures reopen it
    immediately and the Case dead-letters again. Automation never re-enters the outage
    on its own."""
    store = case_store or graph.case_store
    if graph.breaker is not None:
        graph.breaker.reset()
    store.transition(case_id, "approved", actor_id, "replay from dead_letter")
    graph.compiled.invoke(
        Command(goto="write", update={"attempt": 0, "failure_class": None, "past_errors": []}),
        thread_config(case_id),
    )
    return store.get_case(case_id)
