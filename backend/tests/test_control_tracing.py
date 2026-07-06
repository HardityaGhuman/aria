"""§4.4 — boundary traces. `BoundaryTracer` emits one redacted structured event per
meaningful state transition (canonical §14). The point of the layer is *auditability
without exposure*: a reviewer can see request → plan → validation → retrieval → tool
→ answer → outcome, with statuses/budgets/ids, but never the raw message, the final
answer, a document body, a live payload, an email, or chain-of-thought. These tests
pin both halves — that the boundaries are emitted, and that the sensitive strings are
absent."""
import json
import logging

from backend.core import trace
from backend.core.control import models as m
from backend.core.control import tracing as t
from backend.core.tools.principal import Principal


def _ctx(**over) -> m.RequestContext:
    base = dict(
        trace_id="t-abc", principal=Principal(user_id=7, email="e@x.test",
                                               role="employee", region="us"),
        session_id="s-9", message="SECRET how many PTO days do I have?",
        intent="hr", allowed_tiers=("all",), allowed_regions=("global", "us"),
    )
    base.update(over)
    return m.RequestContext(**base)


def _plan() -> m.ReadPlan:
    from backend.services.read_planner import build_plan
    return build_plan("hr")


def _capture(caplog):
    """Parse every JSON telemetry line emitted during the block."""
    recs = []
    for r in caplog.records:
        if r.name == "telemetry":
            recs.append(json.loads(r.getMessage()))
    return recs


# --- events are emitted with the canonical names --------------------------

def test_full_happy_path_emits_the_expected_boundary_sequence(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tr = t.BoundaryTracer(trace_id="t-abc")
    ctx = _ctx()
    plan = _plan()

    tr.request_received(ctx)
    tr.intent_classified("hr")
    tr.plan_built(plan)
    tr.plan_validated(m.ValidationOutcome.proceed())
    tr.specialist_selected("hr-agent")
    tr.retrieval_started("hybrid")
    tr.retrieval_completed(m.StepResult(kind="retrieval", name="hybrid", status="ok",
                                        latency_ms=12, meta={"top_score": 0.83}))
    tr.tool_selected("leave_balance")
    tr.tool_validated(m.ValidationOutcome.proceed())
    tr.tool_completed(m.StepResult(kind="tool", name="leave_balance", status="ok",
                                   latency_ms=30))
    tr.result_validated(m.ValidationOutcome.proceed())
    tr.answer_started()
    tr.answer_completed(latency_ms=900, source_count=2)
    tr.outcome_validated(m.ValidationOutcome.proceed())
    tr.exchange_persisted()
    tr.request_completed(m.RequestOutcome(
        terminal_state=m.TerminalState.OK, answer="SECRET you have 12 days",
        sources=(m.SourceRef(document_id="d1", file="pto.md"),), latency_ms=950))

    recs = _capture(caplog)
    names = [r["event"] for r in recs]
    assert names == [
        "request_received", "intent_classified", "plan_built", "plan_validated",
        "specialist_selected", "retrieval_started", "retrieval_completed",
        "tool_selected", "tool_validated", "tool_completed", "result_validated",
        "answer_started", "answer_completed", "outcome_validated",
        "exchange_persisted", "request_completed",
    ]
    assert all(r["trace_id"] == "t-abc" for r in recs)


def test_every_emitted_name_is_in_the_canonical_enum(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tr = t.BoundaryTracer(trace_id="t-abc")
    tr.request_received(_ctx())
    tr.request_failed(m.TerminalState.INTERNAL_ERROR, error_code="unexpected_error")
    valid = {e.value for e in t.BoundaryEvent}
    for r in _capture(caplog):
        assert r["event"] in valid


# --- redaction: the whole reason the layer exists --------------------------

def test_no_raw_message_answer_email_or_body_in_any_event(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    tr = t.BoundaryTracer(trace_id="t-abc")
    tr.request_received(_ctx(message="SECRET payroll question"))
    tr.plan_built(_plan())
    tr.tool_completed(m.StepResult(kind="tool", name="leave_balance", status="ok",
                                   latency_ms=3, meta={"balance_days": 12}))
    tr.request_completed(m.RequestOutcome(
        terminal_state=m.TerminalState.OK, answer="SECRET you have 12 days",
        sources=(), latency_ms=10))
    blob = json.dumps(_capture(caplog))
    assert "SECRET" not in blob
    assert "e@x.test" not in blob


# --- plan/step/outcome payloads carry the audit-relevant fields ------------

def test_plan_built_carries_version_and_budgets(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    t.BoundaryTracer(trace_id="t-abc").plan_built(_plan())
    rec = _capture(caplog)[0]
    assert rec["plan_version"] == "read-v1"
    assert rec["max_tool_calls"] == 1
    assert rec["max_retrieval_calls"] == 1
    assert rec["timeout_ms"] > 0


def test_retrieval_completed_carries_status_latency_and_ids(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    step = m.StepResult(kind="retrieval", name="hybrid", status="ok", latency_ms=15,
                        meta={"top_score": 0.9, "doc_ids": "hr/a.md,hr/b.md"})
    t.BoundaryTracer(trace_id="t-abc").retrieval_completed(step)
    rec = _capture(caplog)[0]
    assert rec["status"] == "ok"
    assert rec["latency_ms"] == 15
    assert rec["meta"]["top_score"] == 0.9


def test_request_failed_carries_terminal_and_error_code(caplog):
    caplog.set_level(logging.INFO, logger="telemetry")
    t.BoundaryTracer(trace_id="t-abc").request_failed(
        m.TerminalState.GROUNDING_FAILED, error_code="grounding_failed")
    rec = _capture(caplog)[0]
    assert rec["event"] == "request_failed"
    assert rec["terminal_state"] == "grounding_failed"
    assert rec["error_code"] == "grounding_failed"


# --- the kill switch is honored --------------------------------------------

def test_disabled_telemetry_emits_nothing(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="telemetry")
    monkeypatch.setattr(trace.config, "TELEMETRY_ENABLED", False)
    tr = t.BoundaryTracer(trace_id="t-abc")
    tr.request_received(_ctx())
    tr.request_completed(m.RequestOutcome(
        terminal_state=m.TerminalState.OK, answer="x", sources=(), latency_ms=1))
    assert _capture(caplog) == []
