"""§4.3 — deterministic failure policies. Each named failure maps to exactly one
`ValidationAction` (and, when that action ends the request, one concrete
`TerminalState`). No LLM chooses these — the mapping is pinned here, one test per
row of canonical §13, so a regression that quietly turns a `stop` into a `retry`
(or a `blocked` into a `no_results`) fails loudly."""
from backend.core.control import policies as p
from backend.core.control.models import TerminalState, ValidationAction


# --- recoverable degrades: proceed, just less capably ----------------------

def test_query_rewrite_failure_continues_degraded_on_original_query():
    d = p.on_query_rewrite_failure()
    assert d.action is ValidationAction.CONTINUE
    assert d.terminal is None
    assert d.is_terminal is False
    assert d.degraded is True


def test_bm25_unavailable_continues_degraded_vector_only():
    d = p.on_bm25_unavailable()
    assert d.action is ValidationAction.CONTINUE
    assert d.terminal is None
    assert d.degraded is True


# --- tool argument repair: exactly one repair, then stop -------------------

def test_invalid_tool_args_first_time_retries_one_repair():
    d = p.on_invalid_tool_args(repair_attempted=False)
    assert d.action is ValidationAction.RETRY
    assert d.terminal is None


def test_invalid_tool_args_after_repair_stops_internal_error():
    d = p.on_invalid_tool_args(repair_attempted=True)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.INTERNAL_ERROR


# --- unknown / unauthorized tool: stop immediately, plan breach ------------

def test_unauthorized_tool_stops_invalid_plan():
    d = p.on_unauthorized_tool()
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.INVALID_PLAN


# --- HRIS transient: retry once, then policy-only partial (or unavailable) --

def test_hris_transient_first_time_retries():
    d = p.on_hris_transient_failure(attempt=0, can_answer_from_policy=True)
    assert d.action is ValidationAction.RETRY
    assert d.terminal is None


def test_hris_transient_exhausted_with_policy_degrades_to_partial():
    d = p.on_hris_transient_failure(attempt=1, can_answer_from_policy=True)
    assert d.action is ValidationAction.PARTIAL
    assert d.terminal is TerminalState.PARTIAL


def test_hris_transient_exhausted_without_policy_is_tool_unavailable():
    d = p.on_hris_transient_failure(attempt=1, can_answer_from_policy=False)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.TOOL_UNAVAILABLE


def test_hris_no_record_is_a_valid_result_not_a_failure():
    d = p.on_hris_no_record()
    assert d.action is ValidationAction.CONTINUE
    assert d.terminal is None
    assert d.degraded is False


# --- Calendar transient: retry once, then tool_unavailable -----------------

def test_calendar_transient_first_time_retries():
    d = p.on_calendar_transient_failure(attempt=0)
    assert d.action is ValidationAction.RETRY
    assert d.terminal is None


def test_calendar_transient_exhausted_is_tool_unavailable():
    d = p.on_calendar_transient_failure(attempt=1)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.TOOL_UNAVAILABLE


def test_invalid_calendar_range_rejected_before_execution():
    d = p.on_invalid_calendar_range()
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.NO_RESULTS


# --- no authorized evidence: blocked (RBAC) vs no_results (nothing) --------

def test_no_authorized_context_with_restricted_matches_blocks():
    d = p.on_no_authorized_context(restricted_matches_exist=True)
    assert d.action is ValidationAction.BLOCK
    assert d.terminal is TerminalState.BLOCKED


def test_no_authorized_context_with_nothing_is_no_results():
    d = p.on_no_authorized_context(restricted_matches_exist=False)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.NO_RESULTS


# --- budget / grounding / unexpected --------------------------------------

def test_capability_exhausted_stops():
    d = p.on_capability_exhausted()
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.INTERNAL_ERROR


def test_grounding_failed_true_ungrounded_is_grounding_failed():
    d = p.on_grounding_failed(is_no_context_sentinel=False)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.GROUNDING_FAILED


def test_grounding_no_context_sentinel_maps_to_no_results():
    d = p.on_grounding_failed(is_no_context_sentinel=True)
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.NO_RESULTS


def test_unexpected_exception_is_generic_internal_error():
    d = p.on_unexpected_exception()
    assert d.action is ValidationAction.STOP
    assert d.terminal is TerminalState.INTERNAL_ERROR


# --- structural invariants -------------------------------------------------

def test_retry_only_ever_offered_once_never_after_exhaustion():
    # The retry-once contract: any transient policy given a nonzero attempt count
    # must not return RETRY again.
    assert p.on_hris_transient_failure(2, True).action is not ValidationAction.RETRY
    assert p.on_calendar_transient_failure(5).action is not ValidationAction.RETRY


def test_decisions_are_frozen():
    import dataclasses
    d = p.on_unexpected_exception()
    try:
        d.action = ValidationAction.CONTINUE  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("FailureDecision must be immutable")
