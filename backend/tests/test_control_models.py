"""§4.1 / canonical §6 — the control-layer vocabulary. These are pure typed value
objects: the terminal-state set, the versioned plan shape, per-step and validation
records, and the final request outcome. They carry NO document body or chain-of-
thought — only ids, scores, codes, and small typed fields — so a trace built from
them stays safe."""
import dataclasses

import pytest

from backend.core.control import models as m
from backend.core.tools.principal import Principal


def _principal() -> Principal:
    return Principal(user_id=7, email="e@x.test", role="employee", region="us")


def _context(**over) -> m.RequestContext:
    base = dict(
        trace_id="t1", principal=_principal(), session_id="s1",
        message="how many PTO days?", intent="policy",
        allowed_tiers=("all",), allowed_regions=("global", "us"),
    )
    base.update(over)
    return m.RequestContext(**base)


def _plan(**over) -> m.ReadPlan:
    base = dict(
        intent="hr", specialist="hr-agent", allowed_tools=("leave_balance",),
        retrieval=m.RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=1, max_retrieval_calls=1, allows_answer_model=True,
        timeout_ms=8000,
    )
    base.update(over)
    return m.ReadPlan(**base)


def test_plan_version_is_read_v1():
    assert m.PLAN_VERSION == "read-v1"


def test_terminal_states_are_exactly_the_ten():
    expected = {
        "ok", "partial", "no_results", "blocked", "refused",
        "invalid_plan", "tool_unavailable", "grounding_failed",
        "timeout", "internal_error",
    }
    assert {s.value for s in m.TerminalState} == expected


def test_retrieval_requirement_members():
    assert {r.name for r in m.RetrievalRequirement} == {"REQUIRED", "OPTIONAL", "NONE"}


def test_validation_action_members():
    assert {a.value for a in m.ValidationAction} == {
        "continue", "retry", "partial", "block", "stop",
    }


# --- RequestContext -------------------------------------------------------

def test_request_context_is_frozen():
    ctx = _context()
    assert ctx.principal.user_id == 7
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.intent = "hr"  # type: ignore[misc]


def test_request_context_carries_authorization_snapshots():
    ctx = _context()
    assert ctx.allowed_tiers == ("all",)
    assert ctx.allowed_regions == ("global", "us")
    assert isinstance(ctx.allowed_tiers, tuple)
    assert isinstance(ctx.allowed_regions, tuple)


def test_request_context_trace_record_omits_message_and_email():
    ctx = _context(message="secret payroll question")
    rec = ctx.to_trace_record()
    flat = repr(rec)
    assert "secret payroll question" not in flat
    assert "e@x.test" not in flat
    assert rec["trace_id"] == "t1"
    assert rec["intent"] == "policy"
    assert rec["allowed_tiers"] == ("all",)


def test_request_context_trace_record_pseudonymizes_ids_when_flag_on(monkeypatch):
    # The §3.5 privacy default must reach the control serializer too — raw user_id /
    # session_id can correlate a person, so they scrub to salted anon_ tokens.
    from backend.core import trace
    monkeypatch.setattr(trace.config, "TELEMETRY_PSEUDONYMIZE_IDS", True)
    monkeypatch.setattr(trace.config, "TELEMETRY_ID_SALT", "pepper")
    rec = _context().to_trace_record()
    assert rec["user_id"] != 7
    assert str(rec["user_id"]).startswith("anon_")
    assert rec["session_id"] != "s1"
    assert str(rec["session_id"]).startswith("anon_")


def test_request_context_trace_record_passes_ids_through_when_flag_off(monkeypatch):
    from backend.core import trace
    monkeypatch.setattr(trace.config, "TELEMETRY_PSEUDONYMIZE_IDS", False)
    rec = _context().to_trace_record()
    assert rec["user_id"] == 7
    assert rec["session_id"] == "s1"


# --- ReadPlan -------------------------------------------------------------

def test_read_plan_defaults_plan_version_and_is_frozen():
    plan = _plan(intent="policy", specialist="policy-agent", allowed_tools=(),
                 needs_live_data=False, max_tool_calls=0)
    assert plan.plan_version == "read-v1"
    assert plan.allowed_tools == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.max_tool_calls = 5  # type: ignore[misc]


def test_read_plan_has_timeout_assumptions_and_live_data_flag():
    plan = _plan(assumptions=("caller region us",))
    assert plan.timeout_ms == 8000
    assert plan.assumptions == ("caller region us",)
    assert plan.needs_live_data is True
    assert _plan().assumptions == ()
    assert isinstance(_plan().assumptions, tuple)


def test_read_plan_trace_record_is_all_fields():
    rec = _plan().to_trace_record()
    assert rec["intent"] == "hr"
    assert rec["specialist"] == "hr-agent"
    assert rec["allowed_tools"] == ("leave_balance",)
    assert rec["retrieval"] == "required"
    assert rec["needs_live_data"] is True
    assert rec["max_retrieval_calls"] == 1
    assert rec["timeout_ms"] == 8000
    assert rec["plan_version"] == "read-v1"


# --- StepResult -----------------------------------------------------------

def test_step_result_ok_reflects_status():
    good = m.StepResult(kind="retrieval", name="hybrid", status="ok", latency_ms=12)
    bad = m.StepResult(kind="tool", name="leave_balance", status="error",
                       latency_ms=3, error_code="hris_unavailable")
    assert good.ok is True
    assert bad.ok is False
    assert bad.error_code == "hris_unavailable"


def test_step_result_meta_is_frozen_pairs_and_defensively_copied():
    src = {"score": 0.9, "count": 2}
    step = m.StepResult(kind="retrieval", name="hybrid", status="ok",
                        latency_ms=5, meta=src)
    # Stored as an immutable, sorted tuple of (key, scalar) pairs.
    assert step.meta == (("count", 2), ("score", 0.9))
    assert step.meta_map == {"score": 0.9, "count": 2}
    # Mutating the caller's original dict cannot leak into the step.
    src["score"] = 0.0
    assert step.meta_map["score"] == 0.9
    # The record is fully hashable (a mutable-dict field would break this).
    assert hash(step) == hash(step)


def test_step_result_meta_rejects_non_scalar_values():
    # A document body / raw payload must never enter a trace-safe step record.
    with pytest.raises(TypeError):
        m.StepResult(kind="retrieval", name="hybrid", status="ok",
                     latency_ms=1, meta={"body": ["a", "b"]})


def test_step_result_meta_rejects_oversized_string_value():
    # A str IS a scalar, so the non-scalar guard alone can't stop a document body
    # being dumped into meta as a string. A hard length cap fails that closed.
    body = "x" * 5000
    with pytest.raises(ValueError):
        m.StepResult(kind="retrieval", name="hybrid", status="ok",
                     latency_ms=1, meta={"chunk": body})


def test_step_result_meta_allows_normal_short_identifier_strings():
    # Legit meta values — doc ids, section names, strategy/tool names — stay allowed.
    step = m.StepResult(kind="retrieval", name="hybrid", status="ok", latency_ms=1,
                        meta={"doc_id": "legal-compliance/data-protection.md"})
    assert step.meta_map["doc_id"] == "legal-compliance/data-protection.md"


def test_step_result_trace_record_carries_only_safe_fields():
    step = m.StepResult(kind="tool", name="whos_out", status="ok",
                        latency_ms=7, meta={"count": 3})
    rec = step.to_trace_record()
    assert rec["kind"] == "tool"
    assert rec["name"] == "whos_out"
    assert rec["status"] == "ok"
    assert rec["latency_ms"] == 7
    assert rec["meta"] == {"count": 3}


# --- ValidationOutcome ----------------------------------------------------

def test_validation_outcome_proceed_and_halt():
    ok = m.ValidationOutcome.proceed()
    assert ok.valid is True
    assert ok.action is m.ValidationAction.CONTINUE
    assert ok.code is None and ok.reason is None

    stop = m.ValidationOutcome.halt(m.ValidationAction.STOP,
                                    "unknown_specialist", "no such specialist")
    assert stop.valid is False
    assert stop.action is m.ValidationAction.STOP
    assert stop.code == "unknown_specialist"
    assert stop.reason == "no such specialist"


def test_validation_outcome_is_frozen():
    out = m.ValidationOutcome.proceed()
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.valid = False  # type: ignore[misc]


# --- SourceRef ------------------------------------------------------------

def test_source_ref_is_frozen_typed_with_viewer_fields():
    ref = m.SourceRef(document_id="hr/x.md", file="x.md",
                      section="PTO", source_type="all")
    assert ref.document_id == "hr/x.md"
    # viewer fields default empty on the must-ship citation path
    assert ref.chunk_id == ""
    assert ref.page is None
    assert ref.viewer_available is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.file = "y.md"  # type: ignore[misc]


# --- RequestOutcome -------------------------------------------------------

def test_request_outcome_status_maps_terminal_state():
    def status_for(state):
        return m.RequestOutcome(terminal_state=state, answer="a",
                                sources=(), latency_ms=1).status

    # Envelope-visible statuses pass through, each preserved distinctly.
    assert status_for(m.TerminalState.OK) == "ok"
    assert status_for(m.TerminalState.PARTIAL) == "partial"   # NOT collapsed to ok
    assert status_for(m.TerminalState.NO_RESULTS) == "no_results"
    assert status_for(m.TerminalState.BLOCKED) == "blocked"
    assert status_for(m.TerminalState.REFUSED) == "refused"
    assert status_for(m.TerminalState.TOOL_UNAVAILABLE) == "tool_unavailable"  # NOT collapsed to error
    # Internal-only failures stay opaque as a single client-facing "error".
    for state in (
        m.TerminalState.INVALID_PLAN, m.TerminalState.GROUNDING_FAILED,
        m.TerminalState.TIMEOUT, m.TerminalState.INTERNAL_ERROR,
    ):
        assert status_for(state) == "error"


def test_request_outcome_sources_are_typed_refs():
    ref = m.SourceRef(document_id="hr/x.md", file="x.md",
                      section="PTO", source_type="all")
    out = m.RequestOutcome(terminal_state=m.TerminalState.OK, answer="a",
                           sources=(ref,), latency_ms=1)
    assert out.sources[0].document_id == "hr/x.md"


def test_request_outcome_is_frozen():
    out = m.RequestOutcome(terminal_state=m.TerminalState.OK, answer="a",
                           sources=(), latency_ms=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.answer = "b"  # type: ignore[misc]


def test_request_outcome_trace_record_omits_answer_text():
    ref = m.SourceRef(document_id="hr/x.md", file="x.md",
                      section="PTO", source_type="all")
    out = m.RequestOutcome(terminal_state=m.TerminalState.OK,
                           answer="the secret grounded answer body",
                           sources=(ref,), latency_ms=42)
    rec = out.to_trace_record()
    assert "the secret grounded answer body" not in repr(rec)
    assert rec["terminal_state"] == "ok"
    assert rec["status"] == "ok"
    assert rec["latency_ms"] == 42
    assert rec["sources"][0]["document_id"] == "hr/x.md"
