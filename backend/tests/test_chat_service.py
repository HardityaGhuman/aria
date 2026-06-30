"""
tests/test_chat_service.py
--------------------------
Unit tests for backend.services.chat_service.
"""
import asyncio

import backend.services.chat_service as cs
from backend.rag.schema import RetrievedContext


def test_blocked_retrieval_returns_confidential_message_without_llm(monkeypatch):
    monkeypatch.setattr(cs, "retrieve_context",
                        lambda *a, **k: RetrievedContext("", status="blocked", blocked_contact="HR"))

    def _boom(*a, **k):
        raise AssertionError("LLM must not be called on a blocked topic")
    monkeypatch.setattr(cs, "get_llm_response", _boom)
    monkeypatch.setattr(cs, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))

    result = asyncio.run(cs._answer_policy_query("what are L5 salary bands", [], ["all"], ["global", "us"]))
    assert "HR" in result.reply
    assert "confidential" in result.reply.lower() or "restricted" in result.reply.lower()
    assert result.sources == [] and result.context_used == ""
