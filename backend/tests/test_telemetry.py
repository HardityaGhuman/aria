"""Span emission through the _invoke funnel: success, error, and the redaction
invariant (document body must never appear in a telemetry record)."""
import json
import logging

import pytest

from backend.core import llm, trace


class _Usage:
    prompt_tokens = 412
    completion_tokens = 7
    total_tokens = 419


class _Choice:
    class message:  # noqa: N801
        content = "ok"


class _Resp:
    usage = _Usage()
    choices = [_Choice()]


def test_invoke_emits_ok_span(caplog, monkeypatch):
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: _Resp())
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kw: 0.00004)
    token = trace.start_trace(user_id=1, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        resp = llm._invoke("answer", model="big-model", messages=[{"role": "user", "content": "hi"}])
    trace.reset_trace(token)
    assert resp is not None
    rec = json.loads(caplog.records[-1].message)
    assert rec["purpose"] == "answer"
    assert rec["status"] == "ok"
    assert rec["total_tokens"] == 419
    assert rec["cost_usd"] == 0.00004


def test_invoke_emits_error_span_and_reraises(caplog, monkeypatch):
    def boom(**kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(llm.litellm, "completion", boom)
    token = trace.start_trace(user_id=1, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        with pytest.raises(RuntimeError):
            llm._invoke("classify", model="small-model", messages=[])
    trace.reset_trace(token)
    rec = json.loads(caplog.records[-1].message)
    assert rec["status"] == "error"
    assert rec["error_type"] == "RuntimeError"


def test_no_document_body_in_span(caplog, monkeypatch):
    secret = "CONFIDENTIAL_SEVERANCE_FORMULA_42_WEEKS"
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: _Resp())
    monkeypatch.setattr(llm.litellm, "completion_cost", lambda **kw: 0.0)
    token = trace.start_trace(user_id=1, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        llm._invoke("answer", model="big", messages=[{"role": "user", "content": secret}])
    trace.reset_trace(token)
    assert all(secret not in r.message for r in caplog.records)


def _last_request_trace(caplog):
    recs = [json.loads(r.message) for r in caplog.records if '"request_trace"' in r.message]
    return recs[-1]


def test_raw_query_dropped_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(trace.config, "TELEMETRY_LOG_RAW_QUERY", False)
    secret = "my SSN is 123-45-6789"
    token = trace.start_trace(user_id=7, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_request_trace(query=secret, classification="policy", status="ok", total_latency_ms=1)
    trace.reset_trace(token)
    assert all(secret not in r.message for r in caplog.records)
    assert _last_request_trace(caplog)["query"] is None


def test_raw_query_kept_when_flag_on(caplog, monkeypatch):
    monkeypatch.setattr(trace.config, "TELEMETRY_LOG_RAW_QUERY", True)
    token = trace.start_trace(user_id=7, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_request_trace(query="what is PTO?", classification="policy", status="ok", total_latency_ms=1)
    trace.reset_trace(token)
    assert _last_request_trace(caplog)["query"] == "what is PTO?"


def test_ids_pseudonymized_when_flag_on(caplog, monkeypatch):
    monkeypatch.setattr(trace.config, "TELEMETRY_PSEUDONYMIZE_IDS", True)
    monkeypatch.setattr(trace.config, "TELEMETRY_ID_SALT", "pepper")
    token = trace.start_trace(user_id=42, session_id="sess-abc")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_request_trace(query="q", classification="policy", status="ok", total_latency_ms=1)
    trace.reset_trace(token)
    roll = _last_request_trace(caplog)
    assert roll["user_id"] != 42 and roll["user_id"].startswith("anon_")
    assert roll["session_id"] != "sess-abc" and roll["session_id"].startswith("anon_")
    # stable + never leaks the raw id
    assert roll["user_id"] == trace._pseudonymize(42)
    assert all("sess-abc" not in r.message for r in caplog.records)


def test_ids_passthrough_when_flag_off(caplog, monkeypatch):
    monkeypatch.setattr(trace.config, "TELEMETRY_PSEUDONYMIZE_IDS", False)
    token = trace.start_trace(user_id=42, session_id="sess-abc")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        trace.emit_request_trace(query="q", classification="policy", status="ok", total_latency_ms=1)
    trace.reset_trace(token)
    roll = _last_request_trace(caplog)
    assert roll["user_id"] == 42
    assert roll["session_id"] == "sess-abc"


def test_stream_emits_answer_stream_span(caplog, monkeypatch):
    class _StreamUsage:
        prompt_tokens = 900
        completion_tokens = 40
        total_tokens = 940

    class _Delta:
        def __init__(self, content):
            self.content = content

    class _StreamChoice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content=None, usage=None):
            self.choices = [_StreamChoice(content)] if content is not None else []
            self.usage = usage

    fake_stream = [_Chunk("Full"), _Chunk("-time"), _Chunk(usage=_StreamUsage())]
    monkeypatch.setattr(llm.litellm, "completion", lambda **kw: iter(fake_stream))

    token = trace.start_trace(user_id=1, session_id="s")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        out = "".join(llm.stream_llm_response("q", "ctx", history=[]))
    trace.reset_trace(token)

    assert out == "Full-time"
    rec = json.loads(caplog.records[-1].message)
    assert rec["purpose"] == "answer_stream"
    assert rec["model_role"] == "large"
    assert rec["total_tokens"] == 940
