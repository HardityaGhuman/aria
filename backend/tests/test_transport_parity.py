"""Slice 5 of §6: sync/stream transport parity.

The whole point of the shared read pipeline is that `POST /chat` and
`POST /chat/stream` can never disagree on the outcome. Drive both transports with
identical stubs across every branch and assert they produce the same terminal
status, the same (envelope-shaped) sources, and the same answer text.

Only the answer-model call differs by transport (`get_llm_response` returns the whole
string; `stream_llm_response` yields it in chunks), so both are stubbed to emit the
same text — any divergence in status/sources then comes from the pipeline, which is
exactly what must stay identical.
"""
import asyncio

import backend.services.chat_service as cs
import backend.services.read_pipeline as rp
from backend.rag.schema import RetrievedContext

_ANSWER = "Full-time employees accrue 20 PTO days."
_SOURCES = [{"source": "time-and-leave/pto.md", "section": "PTO", "access_tier": "all"}]


def _patch(monkeypatch, classification, retrieved):
    monkeypatch.setattr(rp, "_prepare_history", lambda sid: [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: classification)
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context", lambda *a, **k: retrieved)
    monkeypatch.setattr(rp, "get_meta_response", lambda *a, **k: _ANSWER)
    monkeypatch.setattr(rp, "get_chitchat_response", lambda *a, **k: _ANSWER)
    # Answer model: same text on both transports.
    monkeypatch.setattr(cs, "get_llm_response", lambda *a, **k: _ANSWER)
    monkeypatch.setattr(cs, "stream_llm_response", lambda *a, **k: iter([_ANSWER]))
    monkeypatch.setattr(cs, "append_exchange", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_persist_quietly", lambda *a, **k: asyncio.sleep(0))


def _sync(message):
    r = asyncio.run(cs.generate_chat_reply("s", message, ["all"], ["global"], owner_user_id=1))
    return r.status, cs.to_source_dicts(r.sources), r.reply


def _stream(message):
    async def _run():
        return [e async for e in cs.stream_chat_reply("s", message, ["all"], ["global"], owner_user_id=1)]
    events = asyncio.run(_run())
    done = next(e for e in events if e["event"] == "done")["data"]
    return done["status"], done["sources"], done["answer"]


def _assert_parity(monkeypatch, classification, retrieved, message):
    _patch(monkeypatch, classification, retrieved)
    sync = _sync(message)
    _patch(monkeypatch, classification, retrieved)  # fresh stubs (persist stub is single-shot-safe)
    stream = _stream(message)
    assert sync == stream, f"{classification}: sync={sync} stream={stream}"
    return sync


def test_parity_policy_ok(monkeypatch):
    retrieved = RetrievedContext("CTX", _SOURCES, status="ok")
    status, sources, answer = _assert_parity(monkeypatch, "policy", retrieved, "what is the PTO policy")
    assert status == "ok"
    assert sources[0]["document_id"] == "time-and-leave/pto.md"
    assert answer == _ANSWER


def test_parity_blocked(monkeypatch):
    retrieved = RetrievedContext("", status="blocked", blocked_contact="HR")
    status, sources, _ = _assert_parity(monkeypatch, "policy", retrieved, "L5 salary bands")
    assert status == "blocked"
    assert sources == []


def test_parity_no_results(monkeypatch):
    retrieved = RetrievedContext("", [], status="ok")
    status, sources, _ = _assert_parity(monkeypatch, "policy", retrieved, "moon composition")
    assert status == "no_results"
    assert sources == []


def test_parity_out_of_scope(monkeypatch):
    status, sources, _ = _assert_parity(monkeypatch, "out_of_scope", RetrievedContext("", [], status="ok"), "write me a poem")
    assert status == "refused"
    assert sources == []


def test_parity_meta(monkeypatch):
    status, _, answer = _assert_parity(monkeypatch, "meta", RetrievedContext("", [], status="ok"), "what did you just say")
    assert status == "ok"
    assert answer == _ANSWER
