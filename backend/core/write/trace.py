"""core/write/trace.py
-------------------
The write-path step trace. Closes the gap the 2026-07-13 audit found: the read path
has BoundaryTracer + LLM spans, the write path had NOTHING, so a Case that died at
write_failed was un-debuggable beyond a single `detail` string.

Three layers, deliberately distinct (never merge them):
  state  — the LangGraph checkpoint: what the Case needs to resume.
  audit  — onboarding_case_audit: who did what, for compliance. Append-only.
  trace  — THIS: how the run behaved (nodes, attempts, latencies, failure classes),
           for debugging. Ephemeral, redacted, rides TELEMETRY_ENABLED.

System-owned: emitted by the graph (the orchestrator), never by the LLM or the tool.

Redaction is enforced STRUCTURALLY by ALLOWED_FIELDS, not by reviewer discipline:
any kwarg not on the allowlist is dropped before emission. So a future caller who
passes employee_email= or a raw connector payload leaks nothing. Ids, statuses,
classes, latencies, counts — nothing else ever reaches the sink."""
from backend.core.trace import current_trace, emit_record

# The ONLY keys that may ever be emitted. Anything else is dropped.
ALLOWED_FIELDS = frozenset({
    "case_id", "node", "status", "latency_ms",
    "attempt", "connector", "outcome", "failure_class", "decision",
})


def emit_case_event(event: str, **fields) -> None:
    """Emit one write-path event, dropping every field not on the allowlist."""
    ctx = current_trace()
    record = {"event": event, "trace_id": ctx.trace_id if ctx else None}
    record.update({k: v for k, v in fields.items() if k in ALLOWED_FIELDS})
    emit_record(record)


def case_node_started(case_id: str, node: str) -> None:
    emit_case_event("case_node_started", case_id=case_id, node=node)


def case_node_completed(case_id: str, node: str, status: str, latency_ms: int) -> None:
    emit_case_event("case_node_completed", case_id=case_id, node=node,
                    status=status, latency_ms=latency_ms)


def case_write_attempted(case_id: str, attempt: int, connector: str) -> None:
    emit_case_event("case_write_attempted", case_id=case_id, attempt=attempt,
                    connector=connector)


def case_write_result(case_id: str, attempt: int, outcome: str, latency_ms: int,
                      failure_class: str | None = None) -> None:
    emit_case_event("case_write_result", case_id=case_id, attempt=attempt,
                    outcome=outcome, latency_ms=latency_ms, failure_class=failure_class)


def case_interrupted(case_id: str, node: str) -> None:
    emit_case_event("case_interrupted", case_id=case_id, node=node)


def case_resumed(case_id: str, decision: str) -> None:
    emit_case_event("case_resumed", case_id=case_id, decision=decision)
