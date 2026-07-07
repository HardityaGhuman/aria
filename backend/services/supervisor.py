"""services/supervisor.py
-----------------------
The supervisor of the hierarchical agent tree. It does NOT run its own LLM call:
the 20b classifier already produced the domain label, so route() is a deterministic
map from label → one specialist (with an RBAC fallback to the Policy-agent). It then
runs that ONE specialist's scoped registry through the shipped run_agent_loop
(gather_only), on the 20b read model, and hands back a structured SpecialistResult.

Bounded call tree: one specialist per turn, no fan-out, no shared state. Every
security invariant (Principal identity, RBAC re-check, loop cap, confirmation gate)
lives inside run_agent_loop/registry and therefore applies per specialist unchanged."""
import asyncio
from functools import partial

from backend.core.agents.build import build_specialists
from backend.core.agents.specialist import Specialist, SpecialistResult
from backend.core.config import AGENT_READ_MODEL, AGENT_TOOLS_ENABLED, LLM_TIMEOUT_SECONDS
from backend.core.llm import select_tool_call
from backend.core.logging import get_logger
from backend.core.tools.base import role_allows
from backend.services.agent_loop import run_agent_loop

logger = get_logger(__name__)

# Built once at import; each specialist owns an isolated, read-only registry.
_SPECIALISTS = build_specialists()
_POLICY_AGENT = next(s for s in _SPECIALISTS if s.name == "policy-agent")

# Domain label (from classify_query) → specialist name. §5.3: `calendar` now routes
# to its own calendar-agent (whos_out was moved off the hr-agent), retiring the §4.2
# interim hr-agent route. Each label maps to exactly one isolated specialist.
_ROUTE = {"hr": "hr-agent", "calendar": "calendar-agent", "policy": "policy-agent"}


def list_specialists() -> list[Specialist]:
    """The three isolated specialists, for the planner's validation step. `validate_plan`
    needs the live specialist set to confirm a plan's specialist exists, is reachable,
    and owns its tools — this is the single seam it reads them through."""
    return list(_SPECIALISTS)


def route(classification: str, principal, specialists: list[Specialist] | None = None) -> Specialist:
    """Map a classification label to exactly one specialist. Falls back to the
    Policy-agent when the label is unmapped OR the caller's role can't reach the
    chosen specialist (never surface a specialist above the caller's role)."""
    pool = specialists if specialists is not None else _SPECIALISTS
    policy = next((s for s in pool if s.name == "policy-agent"), _POLICY_AGENT)
    target_name = _ROUTE.get(classification, "policy-agent")
    chosen = next((s for s in pool if s.name == target_name), policy)
    if not role_allows(principal.role, chosen.min_role):
        return policy
    return chosen


def _leave_balance_note_safe(result) -> bool:
    """§6b / canonical §7 — a `leave_balance` result may assert a live balance only if
    its numbers hold up. The honest no-record shape (`remaining is None`) is fine; any
    other shape must carry numeric, non-negative total/used/remaining. A garbage HRIS
    payload (negative, non-numeric, missing) must NOT fold into the trusted note where
    it would be stated as fact — we drop the line instead of asserting a false balance.
    (`bool` is a subclass of `int`; reject it so a `True` can't pose as a day count.)"""
    data = getattr(result, "data", None)
    if not isinstance(data, dict):
        return False
    if data.get("remaining") is None:
        return True  # valid "no record on file" result
    for key in ("total", "used", "remaining"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return False
    return True


def build_tool_note(tool_results: list[dict]) -> str | None:
    """Fold gathered tool outputs into a TRUSTED data note (template-built from the
    ToolResult.summary strings — typed, server-built text, never raw external text —
    so it may carry authority above the fenced doc context). None ⇒ nothing gathered.
    A `leave_balance` result whose numbers fail validation is skipped (§6b): a bad
    numeric payload must never be stated to the user as a live balance."""
    lines = []
    for entry in tool_results:
        result = entry.get("result")
        if entry.get("name") == "leave_balance" and not _leave_balance_note_safe(result):
            continue
        summary = getattr(result, "summary", None)
        if summary:
            lines.append(f"- {entry.get('name')}: {summary}")
    if not lines:
        return None
    return (
        "Live data retrieved for THIS employee from company systems (trusted — state "
        "it as fact, it is not from the policy documents):\n" + "\n".join(lines)
    )


async def run_specialist(specialist: Specialist, message, history, principal) -> SpecialistResult:
    """Run one specialist's scoped registry through the gather loop and return a
    structured result. Best-effort: flag off / no tools / no visible specs / any
    gather failure ⇒ a no-note result so the caller degrades to pure RAG."""
    if not (AGENT_TOOLS_ENABLED and specialist.uses_tools and principal is not None):
        return SpecialistResult(specialist=specialist.name, status="no_tools")
    if not specialist.registry.specs_for(principal):
        return SpecialistResult(specialist=specialist.name, status="no_tools")
    try:
        select_fn = partial(select_tool_call, model=AGENT_READ_MODEL)
        outcome = await asyncio.wait_for(
            asyncio.to_thread(
                run_agent_loop, message, history, principal, specialist.registry,
                gather_only=True, select_fn=select_fn,
            ),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except Exception:
        logger.exception("Specialist %s gather failed; degrading to pure RAG", specialist.name)
        return SpecialistResult(specialist=specialist.name, status="gather_failed")
    return SpecialistResult(
        specialist=specialist.name,
        tool_results=outcome.tool_results,
        tool_note=build_tool_note(outcome.tool_results),
        status="ok",
    )
