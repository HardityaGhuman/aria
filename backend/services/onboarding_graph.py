"""services/onboarding_graph.py
----------------------------
The onboarding write Case as a LangGraph — the SAME spine as leave_graph.py and
jira_graph.py, a third external system. The Case sleeps at `request_approval` (an
`interrupt`) awaiting the manager's click, survives a process restart (Postgres
checkpointer), and resumes at the EXACT interrupted node via `Command(resume=...)`
on `thread_id = onboarding-case-{case_id}` (a third namespace, distinct from leave's
`case-` and jira's `jira-case-`). The LLM appears in exactly one node (`extract`);
every routing/approval/write decision is deterministic Python.

Identity: the state carries `user_id`, NEVER a Principal. It is rebuilt through the
injected `principal_loader` at `validate` and again at `provision` — after the sleep,
so a requester demoted, region-changed, or offboarded while the Case waited is seen as
they are NOW. Loader returns None => fail closed (denied_policy / write_failed).

`build_onboarding_graph` returns an `OnboardingGraph` wrapper bundling the compiled
graph with the case-store it was built against, so `start_case`/`resume_case` read
status from the SAME store the nodes wrote (the store is the source of truth for
status — on an interrupt the graph state has not yet returned the node's status)."""
import time
from dataclasses import dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.core import onboarding_case as onboarding_case_store
from backend.core.access import AccessProvisioner
from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.grant_access import GrantAccessTool
from backend.core.tools.principal import load_principal
from backend.core.write import trace as wt
from backend.services.onboarding_extract import extract_onboarding_fields
from backend.services.onboarding_validator import validate_onboarding

_IDENTITY_GONE = "requester no longer exists"
CONNECTOR = "access-provisioner"


class OnboardingState(TypedDict, total=False):
    case_id: str
    user_id: int                  # identity ANCHOR only — the Principal is reloaded per node
    raw_text: str                 # the only LLM input
    approver_email: str | None
    role: str
    extra_tools: list[str]
    tools: list[str]              # resolved bundle ∪ extras (set by validate)
    validation_ok: bool
    validation_reason: str | None
    decision: str | None          # set by resume: "approve" | "deny"
    decision_actor: str | None
    grant_id: str | None
    # --- execution memory: the control layer's inputs. Set by nodes, never by the LLM.
    attempt: int
    past_errors: list[str]
    failure_class: str | None
    status: str


@dataclass
class OnboardingGraph:
    compiled: object
    case_store: object


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": f"onboarding-case-{case_id}"}}


def build_onboarding_graph(*, provisioner: AccessProvisioner | None = None, checkpointer,
                           extract_fn=None, grant_tool=None,
                           case_store=onboarding_case_store, principal_loader=None) -> OnboardingGraph:
    provisioner = provisioner or MockAccessProvisioner()
    extract_fn = extract_fn or extract_onboarding_fields
    grant_tool = grant_tool or GrantAccessTool(provisioner)
    principal_loader = principal_loader or load_principal

    def extract(state: OnboardingState) -> dict:
        """The ONE LLM call — and it runs at most ONCE per Case.

        The route has already extracted (it needs role+tools to build the idempotency
        key and the Case row the manager will approve), so it seeds them into the
        initial state and this node passes through. Re-running the model here would
        spend a second call to maybe get a DIFFERENT bundle than the one on the row —
        the manager approves one set of tools, the connector grants another. The node
        stays in the graph because a caller who has NOT pre-extracted (any test, any
        future trigger edge) still needs it."""
        if state.get("role"):
            return {}
        started = time.perf_counter()
        wt.case_node_started(state["case_id"], "extract")
        fields = extract_fn(state["raw_text"])
        wt.case_node_completed(state["case_id"], "extract", "ok",
                               int((time.perf_counter() - started) * 1000))
        return {"role": fields["role"], "extra_tools": fields["extra_tools"]}

    def validate(state: OnboardingState) -> dict:
        started = time.perf_counter()
        wt.case_node_started(state["case_id"], "validate")
        # Identity check #1: a requester who vanished between the click and here
        # never reaches the gate.
        if principal_loader(state["user_id"]) is None:
            wt.case_node_completed(state["case_id"], "validate", "identity_gone",
                                   int((time.perf_counter() - started) * 1000))
            return {"validation_ok": False, "validation_reason": _IDENTITY_GONE, "tools": []}
        r = validate_onboarding(state["role"], state.get("extra_tools") or [])
        wt.case_node_completed(state["case_id"], "validate", "ok" if r.ok else "rejected",
                               int((time.perf_counter() - started) * 1000))
        return {"validation_ok": r.ok, "validation_reason": r.reason, "tools": r.tools}

    def deny_policy(state: OnboardingState) -> dict:
        case_store.transition(state["case_id"], "denied_policy", "system",
                              state.get("validation_reason") or "validation failed")
        return {"status": "denied_policy"}

    def request_approval(state: OnboardingState) -> dict:
        # Pre-interrupt work MUST be idempotent: on resume this node re-runs from here.
        # Guard the transition by the current status so a resume doesn't re-transition.
        current = case_store.get_case(state["case_id"])
        if current and current["status"] == "draft":
            case_store.transition(state["case_id"], "pending_approval", "system", "awaiting manager")
        wt.case_interrupted(state["case_id"], "request_approval")
        payload = interrupt({
            "case_id": state["case_id"], "role": state["role"],
            "tools": state["tools"], "approver_email": state.get("approver_email"),
        })
        wt.case_resumed(state["case_id"], payload["decision"])
        return {"decision": payload["decision"], "decision_actor": payload.get("actor_id")}

    def deny_manager(state: OnboardingState) -> dict:
        case_store.transition(state["case_id"], "denied_manager",
                              state.get("decision_actor") or "manager", "denied by manager")
        return {"status": "denied_manager"}

    def provision(state: OnboardingState) -> dict:
        started = time.perf_counter()
        wt.case_node_started(state["case_id"], "provision")
        current = case_store.get_case(state["case_id"])
        if current and current["status"] == "pending_approval":
            case_store.transition(state["case_id"], "approved",
                                  state.get("decision_actor") or "manager", "approved")

        # Identity is reloaded HERE, after the sleep: never write under the role/region
        # the requester had when the Case was filed. Gone user => fail closed, no grant.
        principal = principal_loader(state["user_id"])
        if principal is None:
            case_store.transition(state["case_id"], "write_failed", "system", _IDENTITY_GONE)
            wt.case_node_completed(state["case_id"], "provision", "write_failed",
                                   int((time.perf_counter() - started) * 1000))
            return {"status": "write_failed"}

        attempt = state.get("attempt", 0) + 1
        wt.case_write_attempted(state["case_id"], attempt, CONNECTOR)
        result = grant_tool.invoke({"case_id": state["case_id"], "tools": state["tools"]}, principal)
        latency = int((time.perf_counter() - started) * 1000)
        wt.case_write_result(state["case_id"], attempt, "ok", latency_ms=latency)
        case_store.transition(state["case_id"], "provisioned", "system", "granted",
                              grant_id=result.data["grant_id"], attempt=attempt)
        wt.case_node_completed(state["case_id"], "provision", "provisioned", latency)
        return {"status": "provisioned", "grant_id": result.data["grant_id"], "attempt": attempt}

    # --- routers (pure functions, exhaustive) ---
    def after_validate(state: OnboardingState) -> str:
        return "request_approval" if state["validation_ok"] else "deny_policy"

    def after_approval(state: OnboardingState) -> str:
        return "provision" if state.get("decision") == "approve" else "deny_manager"

    g = StateGraph(OnboardingState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("deny_policy", deny_policy)
    g.add_node("request_approval", request_approval)
    g.add_node("provision", provision)
    g.add_node("deny_manager", deny_manager)

    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["request_approval", "deny_policy"])
    g.add_edge("deny_policy", END)
    g.add_conditional_edges("request_approval", after_approval, ["provision", "deny_manager"])
    g.add_edge("provision", END)
    g.add_edge("deny_manager", END)
    return OnboardingGraph(compiled=g.compile(checkpointer=checkpointer), case_store=case_store)


def start_case(graph: OnboardingGraph, *, case_id, principal, raw_text, approver_email,
               role=None, extra_tools=None, case_store=None):
    """Run the graph to its interrupt (pending_approval) or a terminal denial.

    `role` / `extra_tools` are the route's already-extracted fields. Seeding them makes
    the `extract` node a pass-through, so the model runs EXACTLY once per Case and the
    tools the manager approves are the tools the connector grants. Omit them and the
    graph extracts for itself (tests, and any future non-HTTP trigger)."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        {"case_id": case_id, "user_id": principal.user_id, "raw_text": raw_text,
         "approver_email": approver_email, "attempt": 0, "past_errors": [],
         "role": role or "", "extra_tools": extra_tools or []},
        thread_config(case_id),
    )
    return store.get_case(case_id)


def resume_case(graph: OnboardingGraph, *, case_id, decision, actor_id, case_store=None):
    """Resume the paused Case with the manager's decision; run to terminal status."""
    store = case_store or graph.case_store
    graph.compiled.invoke(
        Command(resume={"decision": decision, "actor_id": actor_id}),
        thread_config(case_id),
    )
    return store.get_case(case_id)
