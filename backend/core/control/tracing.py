"""core/control/tracing.py
-------------------------
§4.4 / canonical §14 — boundary traces.

A boundary trace is the audit spine of a request: one structured event at each
meaningful state transition, so a reviewer can reconstruct *what the system decided
and why* — request → classified intent → plan (+ version/budgets) → validation →
specialist → retrieval → tool → result validation → answer → outcome validation →
persistence → completion/failure — without ever seeing the sensitive content that
flowed through.

Redaction is the whole point. Every payload here is built from the control models'
allowlisted `to_trace_record()` serializers (which already drop the raw message, the
final answer, emails, and scrub correlatable ids) or from plainly-safe scalars
(strategy name, tool name, status, error code, latency). We never pass a raw
`RequestContext`/`RequestOutcome`, a document body, a live HRIS/calendar payload, or
model reasoning into an event.

Transport: reuse the existing telemetry sink (`trace._emit` → the `telemetry` JSON
logger, Cloud Logging-ready) and its `TELEMETRY_ENABLED` kill switch, so boundary
events join LLM spans and the request rollup on `trace_id` in one stream. This file
adds no new sink and makes no independent enable/disable decision.

This is the tracing *primitive*. Wiring a `BoundaryTracer` into the read pipeline at
each transition point is §5/§6 work; the executor owns when to call these methods.
"""
from __future__ import annotations

import enum

from backend.core import trace
from backend.core.control.models import (
    ReadPlan,
    RequestContext,
    RequestOutcome,
    StepResult,
    TerminalState,
    ValidationOutcome,
)


class BoundaryEvent(str, enum.Enum):
    """The finite set of boundary event names (canonical §14). A str-mixin so the
    value serializes as its name; the tracer only ever emits one of these."""
    REQUEST_RECEIVED = "request_received"
    INTENT_CLASSIFIED = "intent_classified"
    PLAN_BUILT = "plan_built"
    PLAN_VALIDATED = "plan_validated"
    SPECIALIST_SELECTED = "specialist_selected"
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    TOOL_SELECTED = "tool_selected"
    TOOL_VALIDATED = "tool_validated"
    TOOL_COMPLETED = "tool_completed"
    RESULT_VALIDATED = "result_validated"
    ANSWER_STARTED = "answer_started"
    ANSWER_COMPLETED = "answer_completed"
    OUTCOME_VALIDATED = "outcome_validated"
    EXCHANGE_PERSISTED = "exchange_persisted"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"


def _validation_fields(outcome: ValidationOutcome) -> dict:
    """The trace-safe slice of a validator verdict. `code`/`reason` are short human
    labels by construction, never a payload."""
    return {
        "valid": outcome.valid,
        "action": outcome.action.value,
        "code": outcome.code,
        "reason": outcome.reason,
    }


class BoundaryTracer:
    """Per-request emitter of boundary events. One instance per request; each method
    marks one transition. `trace_id` defaults to the ambient trace context so a wired
    executor doesn't have to thread it, but can be pinned explicitly (tests, or a
    boundary that runs before `start_trace`)."""

    def __init__(self, trace_id: str | None = None) -> None:
        self._trace_id = trace_id

    def _tid(self) -> str | None:
        if self._trace_id is not None:
            return self._trace_id
        ctx = trace.current_trace()
        return ctx.trace_id if ctx else None

    def _event(self, event: BoundaryEvent, **fields) -> None:
        """Compose one record and hand it to the gated telemetry sink. All keyword
        fields must already be trace-safe (scalars or allowlisted dicts)."""
        trace._emit({"event": event.value, "trace_id": self._tid(), **fields})

    # --- lifecycle boundaries ---------------------------------------------

    def request_received(self, ctx: RequestContext) -> None:
        # to_trace_record omits the raw message + email and scrubs user/session ids.
        self._event(BoundaryEvent.REQUEST_RECEIVED, **ctx.to_trace_record())

    def intent_classified(self, intent: str) -> None:
        self._event(BoundaryEvent.INTENT_CLASSIFIED, intent=intent)

    def plan_built(self, plan: ReadPlan) -> None:
        # Carries plan_version + capability budgets + timeout — the audit of "what was
        # this request allowed to do".
        self._event(BoundaryEvent.PLAN_BUILT, **plan.to_trace_record())

    def plan_validated(self, outcome: ValidationOutcome) -> None:
        self._event(BoundaryEvent.PLAN_VALIDATED, **_validation_fields(outcome))

    def specialist_selected(self, specialist: str | None) -> None:
        self._event(BoundaryEvent.SPECIALIST_SELECTED, specialist=specialist)

    def retrieval_started(self, strategy: str) -> None:
        self._event(BoundaryEvent.RETRIEVAL_STARTED, strategy=strategy)

    def retrieval_completed(self, step: StepResult) -> None:
        # step.to_trace_record carries status/latency/error + meta (ids/scores) only.
        self._event(BoundaryEvent.RETRIEVAL_COMPLETED, **step.to_trace_record())

    def tool_selected(self, tool: str) -> None:
        self._event(BoundaryEvent.TOOL_SELECTED, tool=tool)

    def tool_validated(self, outcome: ValidationOutcome) -> None:
        self._event(BoundaryEvent.TOOL_VALIDATED, **_validation_fields(outcome))

    def tool_completed(self, step: StepResult) -> None:
        self._event(BoundaryEvent.TOOL_COMPLETED, **step.to_trace_record())

    def result_validated(self, outcome: ValidationOutcome) -> None:
        self._event(BoundaryEvent.RESULT_VALIDATED, **_validation_fields(outcome))

    def answer_started(self) -> None:
        self._event(BoundaryEvent.ANSWER_STARTED)

    def answer_completed(self, *, latency_ms: int, source_count: int) -> None:
        # Deliberately NOT the answer text — only how long it took and how many
        # sources it cited. The answer body never enters a trace.
        self._event(BoundaryEvent.ANSWER_COMPLETED,
                    latency_ms=latency_ms, source_count=source_count)

    def outcome_validated(self, outcome: ValidationOutcome) -> None:
        self._event(BoundaryEvent.OUTCOME_VALIDATED, **_validation_fields(outcome))

    def exchange_persisted(self) -> None:
        self._event(BoundaryEvent.EXCHANGE_PERSISTED)

    def request_completed(self, outcome: RequestOutcome) -> None:
        # to_trace_record omits the answer text; keeps terminal/status/latency + source
        # ids.
        self._event(BoundaryEvent.REQUEST_COMPLETED, **outcome.to_trace_record())

    def request_failed(self, terminal_state: TerminalState, *, error_code: str | None) -> None:
        self._event(BoundaryEvent.REQUEST_FAILED,
                    terminal_state=terminal_state.value, error_code=error_code)
