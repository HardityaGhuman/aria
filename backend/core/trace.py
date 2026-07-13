"""
core/trace.py
-------------
Request-scoped tracing for LLM calls (offline observability).

A trace_id lives in a ContextVar so it is auto-copied into the worker threads
that asyncio.to_thread spawns for every LLM call — a span emitted inside a
thread still knows which request it belongs to, with no trace_id argument
threaded through call signatures. Writes inside a worker thread do NOT propagate
back to the parent context, so each span is logged at the point of the call as a
self-contained JSON record; the per-request rollup is built from what
chat_service knows synchronously.

Sink is a dedicated 'telemetry' stdlib logger (stdout JSON). All emission is
best-effort: a logging fault never breaks a chat.

Privacy (production defaults, driven by APP_ENV=production in config):
  - Raw query text is dropped from request traces unless TELEMETRY_LOG_RAW_QUERY
    is force-enabled (queries can carry PII).
  - user_id / session_id are pseudonymized via a salted HMAC (TELEMETRY_ID_SALT)
    so records still correlate but no raw identity reaches the sink.
  - Never emitted anywhere: JWTs, document bodies, raw HRIS/calendar payloads,
    emails. Spans carry only ids + scores + token counts.

Production retention/access expectation: these JSON lines go to Cloud Logging;
treat the request_trace stream as low-sensitivity operational data with a
bounded retention window (30 days) and access limited to the ops role. Do not
route it to any long-term analytics store that widens who can read it.
"""
import contextvars
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass

from backend.core import config

_telemetry_logger = logging.getLogger("telemetry")


def _pseudonymize(value) -> str | None:
    """Stable salted digest of an id so records still correlate but the raw
    identity never reaches the sink. Returns None untouched."""
    if value is None:
        return None
    digest = hmac.new(
        config.TELEMETRY_ID_SALT.encode("utf-8"),
        str(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"anon_{digest[:16]}"


def _scrub_id(value):
    """Pseudonymize an id when the privacy flag is on; otherwise pass through."""
    if config.TELEMETRY_PSEUDONYMIZE_IDS:
        return _pseudonymize(value)
    return value


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    user_id: int | None = None
    session_id: str | None = None


_current: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "trace_ctx", default=None
)


def start_trace(user_id: int | None = None, session_id: str | None = None) -> contextvars.Token:
    """Mint a fresh trace_id and set it as the current context. Returns a token
    to pass to reset_trace (call it in a finally)."""
    ctx = TraceContext(trace_id=uuid.uuid4().hex, user_id=user_id, session_id=session_id)
    return _current.set(ctx)


def current_trace() -> TraceContext | None:
    return _current.get()


def reset_trace(token: contextvars.Token) -> None:
    _current.reset(token)


def emit_record(record: dict) -> None:
    """Emit one JSON telemetry record. Best-effort: a logging fault never breaks a
    request. Public because core/write/trace.py is a second emitter on this sink."""
    if not config.TELEMETRY_ENABLED:
        return
    try:
        _telemetry_logger.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        pass  # telemetry is best-effort; never break the request


_emit = emit_record  # legacy alias: emit_span / emit_request_trace call this


def emit_span(
    purpose: str,
    model: str,
    *,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
    status: str = "ok",
    error_type: str | None = None,
) -> None:
    """Emit one per-LLM-call span. model_role distinguishes the small router
    model from the large answer model — the 'which prompt used which model' signal."""
    ctx = current_trace()
    _emit({
        "event": "llm_span",
        "trace_id": ctx.trace_id if ctx else None,
        "purpose": purpose,
        "model": model,
        "model_role": "small" if model == config.ROUTER_MODEL_NAME else "large",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,
        "status": status,
        "error_type": error_type,
    })


def emit_request_trace(
    *,
    query: str,
    classification: str,
    status: str,
    total_latency_ms: int,
    strategy: str | None = None,
    retrieved: list[dict] | None = None,
) -> None:
    """Emit one per-request rollup. Join to spans on trace_id. Carries the query
    and retrieval ids+scores — never document body."""
    ctx = current_trace()
    _emit({
        "event": "request_trace",
        "trace_id": ctx.trace_id if ctx else None,
        "user_id": _scrub_id(ctx.user_id) if ctx else None,
        "session_id": _scrub_id(ctx.session_id) if ctx else None,
        "query": query if config.TELEMETRY_LOG_RAW_QUERY else None,
        "classification": classification,
        "strategy": strategy,
        "retrieved": retrieved or [],
        "status": status,
        "total_latency_ms": total_latency_ms,
    })
