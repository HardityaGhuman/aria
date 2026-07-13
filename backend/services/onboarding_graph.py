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
from backend.core.config import WRITE_MAX_ATTEMPTS
from backend.core.tools.grant_access import GrantAccessTool
from backend.core.tools.principal import load_principal
from backend.core.write import trace as wt
from backend.core.write.breaker import get_breaker
from backend.core.write.errors import classify_write_error
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
    breaker: object = None


def thread_config(case_id: str) -> dict:
    return {"configurable": {"thread_id": f"onboarding-case-{case_id}"}}


def build_onboarding_graph(*, provisioner: AccessProvisioner | None = None, checkpointer,
                           extract_fn=None, grant_tool=None, case_store=onboarding_case_store,
                           principal_loader=None, breaker=None,
                           max_attempts: int = WRITE_MAX_ATTEMPTS) -> OnboardingGraph:
    provisioner = provisioner or MockAccessProvisioner()
    extract_fn = extract_fn or extract_onboarding_fields
    grant_tool = grant_tool or GrantAccessTool(provisioner)
    principal_loader = principal_loader or load_principal
    breaker = breaker or get_breaker(CONNECTOR)

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
        """The write boundary. Re-entered by the retry edge, so EVERYTHING here must be
        idempotent: the approve-transition is status-guarded, and the grant itself is
        idempotent by case_id (attempt 2 returns attempt 1's grant_id, never a second
        grant). Each attempt is its own checkpoint — that is why retry is an edge and
        not a for-loop."""
        started = time.perf_counter()
        wt.case_node_started(state["case_id"], "provision")
        current = case_store.get_case(state["case_id"])
        if current and current["status"] == "pending_approval":
            case_store.transition(state["case_id"], "approved",
                                  state.get("decision_actor") or "manager", "approved")

        attempt = state.get("attempt", 0) + 1
        past_errors = list(state.get("past_errors") or [])

        # Identity is reloaded HERE, after the sleep: never write under the role/region
        # the requester had when the Case was filed. Gone user => fail closed, no grant.
        # NOTE: this branch does NOT transition the row — it classifies and lets the
        # router send it to the `write_failed` node. Exactly ONE node writes each
        # terminal status; two writers is how a state machine starts lying.
        principal = principal_loader(state["user_id"])
        if principal is None:
            wt.case_node_completed(state["case_id"], "provision", "failed",
                                   int((time.perf_counter() - started) * 1000))
            return {"failure_class": "permanent", "attempt": attempt,
                    "past_errors": past_errors + [_IDENTITY_GONE]}

        # Breaker OPEN => do not touch the connector at all. Classify and let the router
        # send it to the DLQ, where a human can replay once the connector is healthy.
        # Automation halts; work is preserved.
        if breaker.is_open():
            wt.case_write_result(state["case_id"], attempt, "skipped", latency_ms=0,
                                 failure_class="breaker_open")
            wt.case_node_completed(state["case_id"], "provision", "failed",
                                   int((time.perf_counter() - started) * 1000))
            return {"failure_class": "breaker_open", "attempt": attempt,
                    "past_errors": past_errors + ["breaker_open"]}

        wt.case_write_attempted(state["case_id"], attempt, CONNECTOR)
        try:
            result = grant_tool.invoke(
                {"case_id": state["case_id"], "tools": state["tools"]}, principal)
        except Exception as exc:                      # the ONLY place a failure is classified
            failure_class = classify_write_error(exc)
            latency = int((time.perf_counter() - started) * 1000)
            wt.case_write_result(state["case_id"], attempt, "failed", latency_ms=latency,
                                 failure_class=failure_class)
            if failure_class == "transient":
                breaker.record_failure()
            wt.case_node_completed(state["case_id"], "provision", "failed", latency)
            return {"failure_class": failure_class, "attempt": attempt,
                    "past_errors": past_errors + [f"{failure_class}: {type(exc).__name__}"]}

        # EXECUTION IS NOT CORRECTNESS (Ref2 §3). The connector answering without raising
        # is a mechanical fact, not a success. Verify the grant we got back IS the grant
        # that was approved — a grant_id, and exactly the tool set on the Case row. A
        # connector that returns "OK" with an empty or partial payload is the ref's
        # canonical silent failure (200 OK, output: [], recorded as SUCCESS), and marking
        # that `provisioned` would write a lie into the audit log. Permanent: a connector
        # that mis-answers will mis-answer again; a human must look at it.
        latency = int((time.perf_counter() - started) * 1000)
        granted = set((result.data or {}).get("tools") or [])
        if not (result.data or {}).get("grant_id") or granted != set(state["tools"]):
            breaker.record_success()      # the connector RESPONDED; it is not flapping
            wt.case_write_result(state["case_id"], attempt, "unverified", latency_ms=latency,
                                 failure_class="permanent")
            wt.case_node_completed(state["case_id"], "provision", "failed", latency)
            return {"failure_class": "permanent", "attempt": attempt,
                    "past_errors": past_errors + ["permanent: grant did not match request"]}

        breaker.record_success()
        wt.case_write_result(state["case_id"], attempt, "ok", latency_ms=latency)
        case_store.transition(state["case_id"], "provisioned", "system", "granted",
                              grant_id=result.data["grant_id"], attempt=attempt)
        wt.case_node_completed(state["case_id"], "provision", "provisioned", latency)
        return {"status": "provisioned", "grant_id": result.data["grant_id"],
                "attempt": attempt, "failure_class": None}

    def write_failed(state: OnboardingState) -> dict:
        """Permanent failure. Terminal: the connector refused the request and always will.
        A human files a new Case; replaying this one would just fail again."""
        reason = (state.get("past_errors") or ["write failed"])[-1]
        case_store.transition(state["case_id"], "write_failed", "system", reason,
                              attempt=state.get("attempt"), failure_reason="permanent")
        return {"status": "write_failed"}

    def dead_letter(state: OnboardingState) -> dict:
        """Transient failure that survived the budget, or an open breaker. NOT terminal:
        the Case keeps its checkpoint and an admin can replay it (see replay_case)."""
        failure_class = state.get("failure_class") or "transient"
        reason = (state.get("past_errors") or ["transient failure"])[-1]
        case_store.transition(state["case_id"], "dead_letter", "system", reason,
                              attempt=state.get("attempt"), failure_reason=failure_class)
        return {"status": "dead_letter"}

    # --- routers (pure functions, exhaustive) ---
    def after_validate(state: OnboardingState) -> str:
        return "request_approval" if state["validation_ok"] else "deny_policy"

    def after_approval(state: OnboardingState) -> str:
        return "provision" if state.get("decision") == "approve" else "deny_manager"

    def after_provision(state: OnboardingState) -> str:
        """Pure, exhaustive. The retry decision lives HERE, in Python, never in a model.

        Contract with the `provision` node: it writes the row ONLY on success (status
        "provisioned"). Every failure path leaves `status` unset and sets `failure_class`,
        so this router — and only this router — decides stop-vs-retry, and exactly one
        node writes each terminal status."""
        if state.get("status") == "provisioned":
            return END                                   # provision already wrote the row
        failure_class = state.get("failure_class")
        if failure_class == "permanent":
            return "write_failed"                        # never retry what will never work
        if failure_class == "breaker_open":
            return "dead_letter"                         # connector is out; don't spend budget
        if state.get("attempt", 0) >= max_attempts:      # transient, budget spent
            return "dead_letter"
        return "provision"                               # transient, budget left -> retry

    g = StateGraph(OnboardingState)
    g.add_node("extract", extract)
    g.add_node("validate", validate)
    g.add_node("deny_policy", deny_policy)
    g.add_node("request_approval", request_approval)
    g.add_node("provision", provision)
    g.add_node("deny_manager", deny_manager)
    g.add_node("write_failed", write_failed)
    g.add_node("dead_letter", dead_letter)

    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", after_validate, ["request_approval", "deny_policy"])
    g.add_edge("deny_policy", END)
    g.add_conditional_edges("request_approval", after_approval, ["provision", "deny_manager"])
    g.add_conditional_edges("provision", after_provision,
                            ["provision", "write_failed", "dead_letter", END])
    g.add_edge("write_failed", END)
    g.add_edge("dead_letter", END)
    g.add_edge("deny_manager", END)
    return OnboardingGraph(compiled=g.compile(checkpointer=checkpointer),
                           case_store=case_store, breaker=breaker)


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


def replay_case(graph: OnboardingGraph, *, case_id, actor_id, case_store=None):
    """Replay a dead-lettered Case from its checkpoint. The DLQ is a queue, not a
    graveyard: the connector recovered, so re-enter `provision` on the SAME thread —
    no re-extraction, no second approval, no forked audit log. Idempotency by case_id
    means a replay that races a late success cannot double-grant.

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
        Command(goto="provision", update={"attempt": 0, "failure_class": None, "past_errors": []}),
        thread_config(case_id),
    )
    return store.get_case(case_id)
