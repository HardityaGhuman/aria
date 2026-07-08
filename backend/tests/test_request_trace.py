"""One request_trace rollup per chat call, with the right status + retrieval meta.
chat_service is driven with classify/answer stubbed so no real LLM/index is needed."""
import asyncio
import json
import logging

from backend.services import chat_service
from backend.services import read_pipeline


def _drive(monkeypatch, classification, answer="An answer.", sources=None):
    # Shared-pipeline deps live on read_pipeline now; the answer model + persistence
    # stay on the chat_service transport. The rollup itself is still emitted by the
    # transport, which is what these tests assert.
    monkeypatch.setattr(read_pipeline, "_prepare_history", lambda sid: [])
    monkeypatch.setattr(read_pipeline, "classify_query", lambda *a, **k: classification)
    monkeypatch.setattr(chat_service, "append_exchange", lambda *a, **k: None)
    monkeypatch.setattr(read_pipeline, "_preferences_note", lambda uid: None)

    class _Retrieved:
        status = "ok"
        text = "ctx"
        blocked_contact = None
    rsources = sources if sources is not None else [
        {"source": "hr/employment-basics.md", "distance": 0.2, "section": "x", "access_tier": "all"}
    ]
    _Retrieved.sources = rsources
    monkeypatch.setattr(read_pipeline, "retrieve_context", lambda *a, **k: _Retrieved)
    monkeypatch.setattr(read_pipeline, "rewrite_query", lambda *a, **k: "q")
    monkeypatch.setattr(chat_service, "get_llm_response", lambda *a, **k: answer)
    monkeypatch.setattr(read_pipeline, "get_meta_response", lambda *a, **k: answer)
    monkeypatch.setattr(read_pipeline, "get_chitchat_response", lambda *a, **k: answer)


def test_policy_request_emits_one_rollup(monkeypatch, caplog):
    _drive(monkeypatch, "policy")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        asyncio.run(chat_service.generate_chat_reply("sess", "what is PTO?", owner_user_id=3))
    rollups = [json.loads(r.message) for r in caplog.records
               if '"request_completed"' in r.message]
    assert len(rollups) == 1
    roll = rollups[0]
    assert roll["terminal_state"] == "ok"
    assert roll["sources"][0]["document_id"] == "hr/employment-basics.md"


def test_out_of_scope_rollup_has_no_retrieval(monkeypatch, caplog):
    _drive(monkeypatch, "out_of_scope")
    with caplog.at_level(logging.INFO, logger="telemetry"):
        asyncio.run(chat_service.generate_chat_reply("sess", "write me a poem", owner_user_id=3))
    rollups = [json.loads(r.message) for r in caplog.records
               if '"request_completed"' in r.message]
    assert len(rollups) == 1
    assert rollups[0]["terminal_state"] == "refused"
    assert rollups[0]["sources"] == []
