"""core/control/policies.py
--------------------------
§4.3 / canonical §13 — deterministic failure-control policies.

Every failure the read pipeline can hit maps to **exactly one** recovery action,
decided in pure Python. The LLM never chooses whether to retry, degrade, block, or
stop — a prompt injection that could talk the model into "just try again" or "answer
without evidence" would be an escalation, so recovery is a fixed table here, not a
generated decision.

Each policy is a pure function returning a `FailureDecision`:

  - `action`   — the one `ValidationAction` (continue / retry / partial / block / stop).
  - `terminal` — the concrete `TerminalState` **iff** the action ends the request;
                 `None` for continue/retry (the request keeps going).
  - `degraded` — True when we proceeded but with less capability (rewrite/BM25 loss),
                 so the step can be flagged in its trace.
  - `code`/`reason` — short, human-readable, never a payload.

Retry-once contract: a transient policy returns `RETRY` only for the first failure
(`attempt == 0`). The caller passes the running attempt count; any nonzero count
means the single retry was already spent, so the policy resolves to its terminal
fallback (HRIS → partial-or-unavailable, calendar → tool_unavailable). This keeps
"retry once" a property of the code, not of how carefully the caller counts.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.core.control.models import TerminalState, ValidationAction


@dataclass(frozen=True)
class FailureDecision:
    """The deterministic outcome of applying a failure policy. Frozen so a decision
    can't be mutated between being made and being acted on."""
    action: ValidationAction
    terminal: TerminalState | None
    code: str
    reason: str
    degraded: bool = False

    @property
    def is_terminal(self) -> bool:
        """True when this decision ends the request (partial/block/stop resolved to a
        concrete terminal state). Continue/retry keep the request alive → False."""
        return self.terminal is not None


# --- recoverable degrades: proceed, just less capably ----------------------

def on_query_rewrite_failure() -> FailureDecision:
    """The history-aware rewriter failed. Retrieval still runs — on the raw user
    message instead of the standalone rewrite. Never fatal."""
    return FailureDecision(
        ValidationAction.CONTINUE, None,
        "query_rewrite_failed", "retrieving on the original query", degraded=True,
    )


def on_bm25_unavailable() -> FailureDecision:
    """The BM25 cache couldn't be built/loaded. Hybrid degrades to vector-only; the
    request still returns semantically-retrieved evidence."""
    return FailureDecision(
        ValidationAction.CONTINUE, None,
        "bm25_unavailable", "vector-only degraded retrieval", degraded=True,
    )


# --- tool argument repair: exactly one repair, then stop -------------------

def on_invalid_tool_args(repair_attempted: bool) -> FailureDecision:
    """The model emitted a tool call that failed schema validation. First time we
    allow one repair attempt; if the re-emitted call is still invalid we stop with a
    generic structured error (the client never learns the internal reason)."""
    if not repair_attempted:
        return FailureDecision(
            ValidationAction.RETRY, None,
            "tool_args_repair", "one schema-repair attempt",
        )
    return FailureDecision(
        ValidationAction.STOP, TerminalState.INTERNAL_ERROR,
        "tool_args_invalid", "tool arguments still invalid after repair",
    )


def on_unauthorized_tool() -> FailureDecision:
    """The model tried to invoke a tool that isn't in the validated plan's allow-list
    (or that the caller's role can't reach). This is a plan/authorization breach —
    stop immediately, no repair, no retry."""
    return FailureDecision(
        ValidationAction.STOP, TerminalState.INVALID_PLAN,
        "tool_unauthorized", "tool not in the validated plan allow-list",
    )


# --- HRIS transient: retry once, then policy-only partial (or unavailable) --

def on_hris_transient_failure(attempt: int, can_answer_from_policy: bool) -> FailureDecision:
    """The HRIS backend failed transiently while reading a leave balance. Retry once;
    if the retry is spent, degrade to a policy-only `partial` answer when policy
    evidence exists, otherwise there's nothing to return but `tool_unavailable`."""
    if attempt == 0:
        return FailureDecision(
            ValidationAction.RETRY, None,
            "hris_retry", "one HRIS retry",
        )
    if can_answer_from_policy:
        return FailureDecision(
            ValidationAction.PARTIAL, TerminalState.PARTIAL,
            "hris_unavailable_partial", "HRIS unavailable — policy-only partial answer",
        )
    return FailureDecision(
        ValidationAction.STOP, TerminalState.TOOL_UNAVAILABLE,
        "hris_unavailable", "HRIS unavailable and no policy evidence to fall back on",
    )


def on_hris_no_record() -> FailureDecision:
    """HRIS answered successfully but has no leave record for this employee. That is a
    valid result, not a failure — the specialist reports 'no record on file' rather
    than fabricating a balance. Not degraded."""
    return FailureDecision(
        ValidationAction.CONTINUE, None,
        "hris_no_record", "no leave record on file for this employee",
    )


# --- Calendar transient: retry once, then tool_unavailable -----------------

def on_calendar_transient_failure(attempt: int) -> FailureDecision:
    """The calendar backend failed transiently reading who-is-out. Retry once; if the
    retry is spent, there's no mandatory policy fallback for a calendar request, so it
    resolves to `tool_unavailable`."""
    if attempt == 0:
        return FailureDecision(
            ValidationAction.RETRY, None,
            "calendar_retry", "one calendar retry",
        )
    return FailureDecision(
        ValidationAction.STOP, TerminalState.TOOL_UNAVAILABLE,
        "calendar_unavailable", "calendar unavailable after retry",
    )


def on_invalid_calendar_range() -> FailureDecision:
    """The requested date range is invalid (e.g. start after end, or wider than the
    allowed window). Reject deterministically before the tool is ever called — we
    return no calendar results rather than executing an unbounded read."""
    return FailureDecision(
        ValidationAction.STOP, TerminalState.NO_RESULTS,
        "calendar_range_invalid", "date range rejected before tool execution",
    )


# --- no authorized evidence: blocked (RBAC) vs no_results (nothing) --------

def on_no_authorized_context(restricted_matches_exist: bool) -> FailureDecision:
    """Retrieval returned no context the caller may see. If restricted chunks matched
    but were filtered by RBAC, that's a `blocked` (something exists, you can't see it);
    if nothing matched at all, it's a plain `no_results`."""
    if restricted_matches_exist:
        return FailureDecision(
            ValidationAction.BLOCK, TerminalState.BLOCKED,
            "restricted_only", "only restricted evidence matched (RBAC)",
        )
    return FailureDecision(
        ValidationAction.STOP, TerminalState.NO_RESULTS,
        "no_context", "no authorized evidence matched",
    )


# --- budget / grounding / unexpected --------------------------------------

def on_capability_exhausted() -> FailureDecision:
    """The per-request tool/retrieval budget was exhausted without producing a
    groundable result. Stop — the loop cap is a hard bound, not a suggestion."""
    return FailureDecision(
        ValidationAction.STOP, TerminalState.INTERNAL_ERROR,
        "capability_exhausted", "capability budget exhausted",
    )


def on_grounding_failed(is_no_context_sentinel: bool) -> FailureDecision:
    """The final answer didn't ground in the retrieved evidence. If the answer model
    emitted the fixed `__NO_CONTEXT_ANSWER__` sentinel, that's an honest 'I couldn't
    find it' → localized `no_results`. Otherwise the model produced an ungrounded
    answer we refuse to serve → `grounding_failed`."""
    if is_no_context_sentinel:
        return FailureDecision(
            ValidationAction.STOP, TerminalState.NO_RESULTS,
            "answer_no_context", "answer model reported no grounding context",
        )
    return FailureDecision(
        ValidationAction.STOP, TerminalState.GROUNDING_FAILED,
        "grounding_failed", "answer could not be grounded in retrieved evidence",
    )


def on_unexpected_exception() -> FailureDecision:
    """Any unclassified exception. Fail closed with a generic structured error; the
    redacted failure trace (§4.4) carries the detail, the client sees only 'error'."""
    return FailureDecision(
        ValidationAction.STOP, TerminalState.INTERNAL_ERROR,
        "unexpected_error", "unexpected exception",
    )
