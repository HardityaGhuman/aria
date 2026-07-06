"""Tests for SSE streaming (Task 3) — stream_chat_reply's event protocol.

The LLM, retrieval, history, and persistence are monkeypatched so the test
exercises only the event sequencing/contract — no Chroma, Postgres, or network.
"""
import asyncio

import backend.services.chat_service as cs
from backend.rag.schema import RetrievedContext


def _collect(session, message, tiers, regions):
    async def _run():
        return [ev async for ev in cs.stream_chat_reply(session, message, tiers, regions)]
    return asyncio.run(_run())


def _patch_common(monkeypatch):
    # No history/persistence DB; no query-rewrite LLM.
    monkeypatch.setattr(cs, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(cs, "_persist_quietly", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(cs, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))


def test_normal_query_streams_tokens_then_done(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cs, "classify_query", lambda message, history: "policy")
    monkeypatch.setattr(
        cs, "retrieve_context",
        lambda *a, **k: RetrievedContext(
            "ctx",
            [{"source": "time-and-leave/pto.md", "chunk": 1, "access_tier": "all", "section": "PTO"}],
            status="ok",
        ),
    )
    monkeypatch.setattr(cs, "stream_llm_response",
                        lambda *a, **k: iter(["You get ", "20 days."]))

    events = _collect("s1", "pto?", ["all"], ["global", "us"])

    tokens = [e["data"]["delta"] for e in events if e["event"] == "token"]
    assert "".join(tokens) == "You get 20 days."

    assert any(e["event"] == "sources" for e in events)

    done = events[-1]
    assert done["event"] == "done"
    assert done["data"]["answer"] == "You get 20 days."
    assert done["data"]["status"] == "ok"
    assert done["data"]["session_id"] == "s1"
    assert isinstance(done["data"]["latency_ms"], int)
    assert done["data"]["sources"][0]["document_id"] == "time-and-leave/pto.md"
    assert done["data"]["sources"][0]["source_type"] == "all"


def test_blocked_query_emits_single_done_no_tokens(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cs, "classify_query", lambda message, history: "policy")
    monkeypatch.setattr(
        cs, "retrieve_context",
        lambda *a, **k: RetrievedContext("", status="blocked", blocked_contact="HR"),
    )

    def _boom(*a, **k):
        raise AssertionError("LLM must not stream on a blocked topic")
    monkeypatch.setattr(cs, "stream_llm_response", _boom)

    events = _collect("s1", "L5 salary bands?", ["all"], ["global", "us"])

    assert not any(e["event"] == "token" for e in events)
    assert len(events) == 1
    assert events[0]["event"] == "done"
    assert events[0]["data"]["status"] == "blocked"
    assert "HR" in events[0]["data"]["answer"]


def test_sentinel_reply_never_leaks_and_maps_to_no_results(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cs, "classify_query", lambda message, history: "policy")
    monkeypatch.setattr(
        cs, "retrieve_context",
        lambda *a, **k: RetrievedContext(
            "ctx", [{"source": "x.md", "chunk": 1, "access_tier": "all", "section": "S"}], status="ok",
        ),
    )
    # Model emits only the sentinel, split across tokens.
    monkeypatch.setattr(cs, "stream_llm_response",
                        lambda *a, **k: iter(list(cs.NO_CONTEXT_SENTINEL)))

    events = _collect("s1", "what is the moon made of", ["all"], ["global", "us"])

    # No token (or done answer) may leak the raw sentinel.
    for e in events:
        if e["event"] == "token":
            assert cs.NO_CONTEXT_SENTINEL not in e["data"]["delta"]
    done = events[-1]
    assert done["event"] == "done"
    assert done["data"]["status"] == "no_results"
    assert done["data"]["sources"] == []
    assert cs.NO_CONTEXT_SENTINEL not in done["data"]["answer"]
    assert done["data"]["answer"] == cs.NO_RESULTS_MESSAGE  # English default


def test_no_retrieval_results_message_is_localized(monkeypatch):
    # Parity with the sync path: a Spanish-language user must get the Spanish
    # "couldn't find that" message on the streaming no-results branch too.
    _patch_common(monkeypatch)
    monkeypatch.setattr(cs, "classify_query", lambda message, history: "policy")
    monkeypatch.setattr(cs, "retrieve_context",
                        lambda *a, **k: RetrievedContext("", [], status="ok"))
    monkeypatch.setattr(cs, "_user_language", lambda owner_user_id: "Spanish")

    events = _collect("s1", "una pregunta sin respuesta", ["all"], ["global", "us"])

    done = events[-1]
    assert done["event"] == "done"
    assert done["data"]["status"] == "no_results"
    assert done["data"]["answer"] == cs._NO_RESULTS_BY_LANGUAGE["Spanish"]


def test_empty_message_emits_error_event(monkeypatch):
    _patch_common(monkeypatch)
    events = _collect("s1", "   ", ["all"], ["global", "us"])
    assert len(events) == 1
    assert events[0]["event"] == "error"
    # SSE errors use the SAME nested envelope as the REST path: {"error": {...}}.
    assert events[0]["data"]["error"]["code"] == "validation_error"
    assert isinstance(events[0]["data"]["error"]["message"], str)
