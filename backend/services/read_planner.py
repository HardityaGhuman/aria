"""services/read_planner.py
-------------------------
§4.2 — the deterministic plan table.

A classifier hands us an *intent* label. This module turns that label into a typed,
versioned `ReadPlan` in **pure Python** — no second LLM call, no model-chosen
specialist / tools / budgets (canonical §7). `validate_plan` then checks that a
built (or hand-supplied) plan is internally consistent, within the global capability
caps, and that its tools actually live in its specialist's registry and are visible
to the caller — BEFORE the executor runs anything. A plan that fails validation ends
the request in `invalid_plan`; it never executes.

Why a table and not the model: budgets and permissions are a security boundary. If
the LLM could widen a budget or pick a specialist, a prompt injection could escalate
a read into a broader action. The mapping is fixed here so behavior is bounded and
auditable regardless of what the model emits.
"""
from __future__ import annotations

from backend.core.config import LLM_TIMEOUT_SECONDS
from backend.core.control.models import (
    PLAN_VERSION,
    ReadPlan,
    RetrievalRequirement,
    ValidationAction,
    ValidationOutcome,
)
from backend.core.tools.base import role_allows

# Global hard caps. A per-intent budget can never exceed these no matter what the
# table (or a hand-built plan) says — the rescope allows at most one tool read and
# one retrieval per request.
GLOBAL_MAX_TOOL_CALLS = 1
GLOBAL_MAX_RETRIEVAL_CALLS = 1

# One answer-model budget in milliseconds, derived from the shared LLM timeout.
_ANSWER_TIMEOUT_MS = LLM_TIMEOUT_SECONDS * 1000

# The fixed plan table (canonical §7). Keyed by classifier intent. Each entry is the
# full set of fields that differ per intent; build_plan stamps version + timeout.
#            specialist        tools              retrieval                        live   tool ret  answer
_TABLE: dict[str, tuple] = {
    "policy":   ("policy-agent",  (),               RetrievalRequirement.REQUIRED, False, 0,  1,  True),
    "hr":       ("hr-agent",      ("leave_balance",), RetrievalRequirement.REQUIRED, True,  1,  1,  True),
    # calendar retrieval is OPTIONAL (policy may or may not fuse in), so it keeps a
    # single retrieval budget — OPTIONAL with a zero budget would be self-contradictory.
    "calendar": ("calendar-agent", ("whos_out",),   RetrievalRequirement.OPTIONAL, True,  1,  1,  True),
    "meta":     (None,            (),               RetrievalRequirement.NONE,     False, 0,  0,  True),
    "chitchat": (None,            (),               RetrievalRequirement.NONE,     False, 0,  0,  True),
    # out_of_scope answers with a fixed refusal — no answer-model call at all.
    "out_of_scope": (None,        (),               RetrievalRequirement.NONE,     False, 0,  0,  False),
}

_DEFAULT_INTENT = "policy"


def build_plan(intent: str) -> ReadPlan:
    """Map a classifier intent to its fixed `ReadPlan`. An unknown label normalizes
    to `policy` (canonical §7) and the normalization is recorded as an explicit
    assumption so a trace explains the fallback rather than hiding it."""
    assumptions: tuple[str, ...] = ()
    resolved = intent
    if intent not in _TABLE:
        assumptions = (f"normalized unknown intent {intent!r} to {_DEFAULT_INTENT}",)
        resolved = _DEFAULT_INTENT

    specialist, tools, retrieval, live, max_tools, max_retr, answers = _TABLE[resolved]
    return ReadPlan(
        intent=resolved,
        specialist=specialist,
        allowed_tools=tools,
        retrieval=retrieval,
        needs_live_data=live,
        max_tool_calls=max_tools,
        max_retrieval_calls=max_retr,
        allows_answer_model=answers,
        timeout_ms=_ANSWER_TIMEOUT_MS,
        assumptions=assumptions,
    )


def validate_plan(plan, principal, specialists) -> ValidationOutcome:
    """Reject a plan that can't be safely executed. Checks, in order: plan version,
    budgets ≤ global caps, budget/allow-list/retrieval internal consistency, and —
    for a specialist plan — that the specialist exists, the caller may reach it, and
    every allowed tool lives in its registry and is role-visible. Returns a STOP
    verdict (→ `invalid_plan`) on the first failure, else CONTINUE."""
    def stop(code: str, reason: str) -> ValidationOutcome:
        return ValidationOutcome.halt(ValidationAction.STOP, code, reason)

    if plan.plan_version != PLAN_VERSION:
        return stop("plan_version_mismatch",
                    f"plan version {plan.plan_version!r} != {PLAN_VERSION!r}")

    # Budgets must be non-negative and within the global caps.
    if not (0 <= plan.max_tool_calls <= GLOBAL_MAX_TOOL_CALLS):
        return stop("budget_exceeds_cap",
                    f"max_tool_calls {plan.max_tool_calls} outside [0, {GLOBAL_MAX_TOOL_CALLS}]")
    if not (0 <= plan.max_retrieval_calls <= GLOBAL_MAX_RETRIEVAL_CALLS):
        return stop("budget_exceeds_cap",
                    f"max_retrieval_calls {plan.max_retrieval_calls} outside [0, {GLOBAL_MAX_RETRIEVAL_CALLS}]")

    # An allow-list and its tool budget must agree (tools ⇔ a positive tool budget).
    if bool(plan.allowed_tools) != (plan.max_tool_calls > 0):
        return stop("invalid_plan", "allowed_tools and max_tool_calls disagree")

    # Retrieval requirement and its budget must agree.
    if plan.retrieval is RetrievalRequirement.NONE:
        if plan.max_retrieval_calls != 0:
            return stop("invalid_plan", "retrieval NONE with a nonzero retrieval budget")
    else:
        if plan.max_retrieval_calls < 1:
            return stop("invalid_plan", "retrieval required/optional with no retrieval budget")

    # A no-specialist plan (meta/chitchat/out_of_scope) may not carry tools/retrieval.
    if plan.specialist is None:
        if plan.allowed_tools or plan.retrieval is not RetrievalRequirement.NONE:
            return stop("invalid_plan", "no-specialist plan cannot carry tools or retrieval")
        return ValidationOutcome.proceed()

    # A specialist plan: the specialist must exist, be reachable, and own its tools.
    specialist = next((s for s in specialists if s.name == plan.specialist), None)
    if specialist is None:
        return stop("unknown_specialist", f"no specialist named {plan.specialist!r}")
    if not role_allows(principal.role, specialist.min_role):
        return stop("specialist_forbidden",
                    f"role {principal.role!r} cannot reach {specialist.name!r}")

    for tool_name in plan.allowed_tools:
        tool = specialist.registry.get(tool_name)
        if tool is None:
            return stop("tool_not_in_registry",
                        f"{tool_name!r} not in {specialist.name!r} registry")
        if not role_allows(principal.role, tool.min_role):
            return stop("tool_forbidden",
                        f"role {principal.role!r} cannot use {tool_name!r}")

    return ValidationOutcome.proceed()
