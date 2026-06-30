"""Trace context + telemetry emit primitives.

The propagation test is load-bearing: every LLM call runs under
asyncio.to_thread, so a ContextVar set on the request task MUST be readable
inside the worker thread, or spans would lose their trace_id."""
import asyncio
import json
import logging

from backend.core import trace


def test_start_sets_unique_trace_ids():
    t1 = trace.start_trace(user_id=1, session_id="a")
    id1 = trace.current_trace().trace_id
    trace.reset_trace(t1)
    t2 = trace.start_trace(user_id=2, session_id="b")
    id2 = trace.current_trace().trace_id
    trace.reset_trace(t2)
    assert id1 != id2
    assert len(id1) >= 16


def test_reset_restores_none():
    token = trace.start_trace(user_id=1, session_id="a")
    assert trace.current_trace() is not None
    trace.reset_trace(token)
    assert trace.current_trace() is None


def test_trace_propagates_across_to_thread():
    async def main():
        token = trace.start_trace(user_id=1, session_id="s")
        outer = trace.current_trace().trace_id
        inner = await asyncio.to_thread(lambda: trace.current_trace().trace_id)
        trace.reset_trace(token)
        return outer, inner

    outer, inner = asyncio.run(main())
    assert outer == inner


def test_emit_span_writes_json_with_model_role(caplog):
    from backend.core.config import ROUTER_MODEL_NAME
    token = trace.start_trace(user_id=1, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_span("classify", ROUTER_MODEL_NAME, latency_ms=12, total_tokens=400)
    trace.reset_trace(token)
    rec = json.loads(caplog.records[-1].message)
    assert rec["event"] == "llm_span"
    assert rec["purpose"] == "classify"
    assert rec["model_role"] == "small"
    assert rec["total_tokens"] == 400
    assert rec["trace_id"]


def test_emit_request_trace_carries_context(caplog):
    token = trace.start_trace(user_id=7, session_id="abc")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_request_trace(
            query="pto?", classification="policy", status="ok",
            total_latency_ms=1840, strategy="hybrid",
            retrieved=[{"doc_id": "time-and-leave/working-hours-and-pto.md", "score": 0.31}],
        )
    trace.reset_trace(token)
    rec = json.loads(caplog.records[-1].message)
    assert rec["event"] == "request_trace"
    assert rec["user_id"] == 7
    assert rec["session_id"] == "abc"
    assert rec["retrieved"][0]["doc_id"].endswith("working-hours-and-pto.md")


def test_emit_disabled_is_silent(caplog, monkeypatch):
    from backend.core import config
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_span("classify", "x", latency_ms=1)
    assert not caplog.records
