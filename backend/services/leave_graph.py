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
from backend.core.hris import HRISClient
from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal
from backend.core.tools.submit_leave import SubmitLeaveTool
from backend.services.leave_extract import extract_leave_fields
from backend.services.leave_validator import validate_leave


class LeaveState(TypedDict, total=False):
    case_id: str
    principal: Principal          # server-built, never from LLM
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
    status: str


@dataclass
class LeaveGraph:
    compiled: object
    case_store: object


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": f"case-{case_id}"}}


def build_leave_graph(*, hris: HRISClient | None = None, checkpointer, extract_fn=None,
                      submit_tool=None, case_store=leave_case_store, today: date | None = None) -> LeaveGraph:
    hris = hris or MockHRIS()
    extract_fn = extract_fn or extract_leave_fields
    submit_tool = submit_tool or SubmitLeaveTool(hris)

    def extract(state: LeaveState) -> dict:
        fields = extract_fn(state["raw_text"])
        return {"start_date": fields["start_date"], "end_date": fields["end_date"],
                "reason": fields["reason"]}

    def validate(state: LeaveState) -> dict:
        r = validate_leave(state["principal"], hris, state["start_date"], state["end_date"], today=today)
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

    def book(state: LeaveState) -> dict:
        case_store.transition(state["case_id"], "approved", state.get("decision_actor") or "manager", "approved")
        result = submit_tool.invoke(
            {"case_id": state["case_id"], "start_date": state["start_date"],
             "end_date": state["end_date"], "days": state["days"]},
            state["principal"],
        )
        if result.status != "ok":
            case_store.transition(state["case_id"], "write_failed", "system", result.error or "write failed")
            return {"status": "write_failed"}
        case_store.transition(state["case_id"], "booked", "system", "booked",
                              confirmation_id=result.data["confirmation_id"])
        return {"status": "booked", "confirmation_id": result.data["confirmation_id"]}

    def deny_manager(state: LeaveState) -> dict:
        case_store.transition(state["case_id"], "denied_manager",
                              state.get("decision_actor") or "manager", "denied by manager")
        return {"status": "denied_manager"}

    # --- routers (pure functions, exhaustive) ---
    def after_validate(state: LeaveState) -> str:
        return "request_approval" if state["validation_ok"] else "deny_policy"

    def after_approval(state: LeaveState) -> str:
        return "book" if state.get("decision") == "approve" else "deny_manager"

    g = StateGraph(LeaveState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("deny_policy", deny_policy)
    g.add_node("request_approval", request_approval)
    g.add_node("book", book)
    g.add_node("deny_manager", deny_manager)

    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["request_approval", "deny_policy"])
    g.add_edge("deny_policy", END)
    g.add_conditional_edges("request_approval", after_approval, ["book", "deny_manager"])
    g.add_edge("book", END)
    g.add_edge("deny_manager", END)
    return LeaveGraph(compiled=g.compile(checkpointer=checkpointer), case_store=case_store)


def start_case(graph: LeaveGraph, *, case_id, principal, raw_text, approver_email, case_store=None):
    """Run the graph to its interrupt (pending_approval) or a terminal denial."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        {"case_id": case_id, "principal": principal, "raw_text": raw_text,
         "approver_email": approver_email},
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
