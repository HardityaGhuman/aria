"""services/jira_graph.py
----------------------
The Jira write Case as a LangGraph — the SAME spine as leave_graph.py, a second external
system. The Case sleeps at `request_approval` (an `interrupt`) awaiting the project
approver's click, survives a process restart (Postgres checkpointer), and resumes at the
EXACT interrupted node via `Command(resume=...)` on `thread_id = jira-case-{case_id}`
(distinct namespace from leave's `case-`). The LLM appears in exactly one node
(`extract`); every routing/approval/write decision is deterministic Python.

`build_jira_graph` returns a `JiraGraph` wrapper bundling the compiled graph with the
case-store it was built against, so `start_case`/`resume_case` read status from the SAME
store the nodes wrote (the store is the source of truth for status — on an interrupt the
graph state has not yet returned the node's status field)."""
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.core import jira_case as jira_case_store
from backend.core.config import WRITE_MAX_ATTEMPTS
from backend.core.jira import JiraClient
from backend.core.jira.mock import MockJira
from backend.core.jira_case import JIRA_SPEC
from backend.core.tools.create_jira_issue import CreateJiraIssueTool
from backend.core.tools.principal import load_principal
from backend.core.write.breaker import get_breaker
from backend.core.write.node import (
    WriteSpec,
    adapt_store,
    make_terminal_nodes,
    make_write_node,
    make_write_router,
)
from backend.services.jira_extract import extract_jira_fields
from backend.services.jira_validator import validate_jira

_IDENTITY_GONE = "requester no longer exists"
CONNECTOR = "jira"


class JiraState(TypedDict, total=False):
    case_id: str
    user_id: int                  # identity ANCHOR only — the Principal is reloaded per node
    raw_text: str                 # the only LLM input
    approver_email: str | None
    project: str
    issue_type: str
    summary: str
    description: str
    validation_ok: bool
    validation_reason: str | None
    decision: str | None          # set by resume: "approve" | "deny"
    decision_actor: str | None
    issue_key: str | None
    # --- execution memory: the control layer's inputs. Set by nodes, never by the LLM.
    attempt: int
    past_errors: list[str]
    failure_class: str | None
    status: str


@dataclass
class JiraGraph:
    compiled: object
    case_store: object
    breaker: object = None


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": f"jira-case-{case_id}"}}


def build_jira_graph(*, jira: JiraClient | None = None, checkpointer, extract_fn=None,
                     submit_tool=None, case_store=jira_case_store, principal_loader=None,
                     breaker=None, max_attempts: int = WRITE_MAX_ATTEMPTS) -> JiraGraph:
    jira = jira or MockJira()
    extract_fn = extract_fn or extract_jira_fields
    submit_tool = submit_tool or CreateJiraIssueTool(jira)
    principal_loader = principal_loader or load_principal
    breaker = breaker or get_breaker(CONNECTOR)

    def extract(state: JiraState) -> dict:
        fields = extract_fn(state["raw_text"])
        return {"project": fields["project"], "issue_type": fields["issue_type"],
                "summary": fields["summary"], "description": fields["description"]}

    def validate(state: JiraState) -> dict:
        r = validate_jira({"project": state["project"], "issue_type": state["issue_type"],
                           "summary": state["summary"], "description": state["description"]})
        return {"validation_ok": r.ok, "validation_reason": r.reason}

    def deny_validation(state: JiraState) -> dict:
        case_store.transition(state["case_id"], "denied_policy", "system",
                              state.get("validation_reason") or "validation failed")
        return {"status": "denied_policy"}

    def request_approval(state: JiraState) -> dict:
        # Pre-interrupt work MUST be idempotent: on resume this node re-runs from here.
        # Guard the transition by the current status so a resume doesn't re-transition.
        current = case_store.get_case(state["case_id"])
        if current and current["status"] == "draft":
            case_store.transition(state["case_id"], "pending_approval", "system", "awaiting approver")
        payload = interrupt({
            "case_id": state["case_id"], "project": state["project"],
            "issue_type": state["issue_type"], "summary": state["summary"],
            "approver_email": state.get("approver_email"),
        })
        return {"decision": payload["decision"], "decision_actor": payload.get("actor_id")}

    # The write boundary is THE shared one (`core/write/node.py`). Jira supplies only what is
    # agent-specific: which connector, how to call it, and what "the write the approver signed
    # off on" means. Before this, `create` made ONE call and any error — a timeout, a 503 —
    # ended an approved work request at write_failed with no retry and no way back.
    engine = adapt_store(case_store)
    write_spec = WriteSpec(
        connector=CONNECTOR,
        invoke=lambda state, principal: submit_tool.invoke(
            {"case_id": state["case_id"], "project": state["project"],
             "issue_type": state["issue_type"], "summary": state["summary"],
             "description": state["description"]}, principal),
        # An issue_key whose project is not the approved project means the work was filed
        # against the wrong team. Jira answered; it did not do what was approved.
        verify=lambda result, state: bool((result.data or {}).get("issue_key"))
        and (result.data or {})["issue_key"].split("-")[0] == state["project"],
        success_status="created",
        result_field="issue_key",
    )
    write = make_write_node(write_spec, JIRA_SPEC, breaker=breaker,
                            principal_loader=principal_loader, store=engine)
    after_write = make_write_router(max_attempts)
    write_failed, dead_letter = make_terminal_nodes(JIRA_SPEC, store=engine)

    def deny_approver(state: JiraState) -> dict:
        case_store.transition(state["case_id"], "denied_manager",
                              state.get("decision_actor") or "approver", "denied by approver")
        return {"status": "denied_manager"}

    # --- routers (pure functions, exhaustive) ---
    def after_validate(state: JiraState) -> str:
        return "request_approval" if state["validation_ok"] else "deny_validation"

    def after_approval(state: JiraState) -> str:
        return "write" if state.get("decision") == "approve" else "deny_approver"

    g = StateGraph(JiraState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("deny_validation", deny_validation)
    g.add_node("request_approval", request_approval)
    g.add_node("write", write)
    g.add_node("deny_approver", deny_approver)
    g.add_node("write_failed", write_failed)
    g.add_node("dead_letter", dead_letter)

    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["request_approval", "deny_validation"])
    g.add_edge("deny_validation", END)
    g.add_conditional_edges("request_approval", after_approval, ["write", "deny_approver"])
    g.add_conditional_edges("write", after_write,
                            ["write", "write_failed", "dead_letter", END])
    g.add_edge("write_failed", END)
    g.add_edge("dead_letter", END)
    g.add_edge("deny_approver", END)
    return JiraGraph(compiled=g.compile(checkpointer=checkpointer), case_store=case_store,
                     breaker=breaker)


def start_case(graph: JiraGraph, *, case_id, principal, raw_text, approver_email, case_store=None):
    """Run the graph to its interrupt (pending_approval) or a terminal denial."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        {"case_id": case_id, "user_id": principal.user_id, "raw_text": raw_text,
         "approver_email": approver_email, "attempt": 0, "past_errors": []},
        thread_config(case_id),
    )
    return store.get_case(case_id)


def resume_case(graph: JiraGraph, *, case_id, decision, actor_id, case_store=None):
    """Resume the paused Case with the approver's decision; run to terminal status."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        Command(resume={"decision": decision, "actor_id": actor_id}),
        thread_config(case_id),
    )
    return store.get_case(case_id)


def replay_case(graph: JiraGraph, *, case_id, actor_id, case_store=None):
    """Replay a dead-lettered Case from its checkpoint — same contract as leave's replay:
    re-enter the write on the SAME thread (no re-extraction, no second approval, no forked
    audit log), and CLEAR the breaker first, because a Case that exhausted its transient
    budget is itself N consecutive failures and would otherwise short-circuit straight back
    to dead_letter, leaving the DLQ undrainable. The human replaying IS the explicit
    clearance the breaker demands; if they are wrong, the next failures reopen it."""
    store = case_store or graph.case_store
    if graph.breaker is not None:
        graph.breaker.reset()
    store.transition(case_id, "approved", actor_id, "replay from dead_letter")
    graph.compiled.invoke(
        Command(goto="write", update={"attempt": 0, "failure_class": None, "past_errors": []}),
        thread_config(case_id),
    )
    return store.get_case(case_id)
