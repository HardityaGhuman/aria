"""
tests/test_chat_service.py
--------------------------
Unit tests for backend.services.chat_service.
"""
import asyncio

import backend.services.chat_service as cs
from backend.rag.schema import RetrievedContext


# --- Fix 2: the out-of-scope refusal must honor the user's language ---
# REFUSAL_MESSAGE is a hardcoded English constant returned without an LLM call,
# so a Hindi/Spanish user got an English refusal while every other route spoke
# their language.

def test_localized_refusal_translates_known_languages():
    assert cs._localized_refusal("English") == cs.REFUSAL_MESSAGE
    spanish = cs._localized_refusal("Spanish")
    assert spanish != cs.REFUSAL_MESSAGE
    assert "política" in spanish.lower()
    hindi = cs._localized_refusal("Hindi")
    assert hindi != cs.REFUSAL_MESSAGE
    assert any("ऀ" <= ch <= "ॿ" for ch in hindi)  # Devanagari present


def test_localized_refusal_falls_back_to_english_for_unknown():
    assert cs._localized_refusal("Klingon") == cs.REFUSAL_MESSAGE
    assert cs._localized_refusal(None) == cs.REFUSAL_MESSAGE


# --- Fix 3: contentless filler must ask for clarification, not fabricate a search ---
# "umm" was classified policy and the rewriter invented "remote work policy
# eligibility requirements", retrieving garbage. Bare filler should short-circuit
# to a clarification before classification/retrieval.

def test_is_low_content_message_detects_filler():
    for filler in ["umm", "uh", "hmm", "idk", "ok", "hmm ok", "???", "  ", ""]:
        assert cs._is_low_content_message(filler), filler


def test_is_low_content_message_passes_real_questions():
    for real in ["what is the PTO policy", "elaborate", "remote work", "explain it better"]:
        assert not cs._is_low_content_message(real), real


def test_localized_clarify_translates_and_falls_back():
    assert cs._localized_clarify("English") == cs.CLARIFY_MESSAGE
    assert cs._localized_clarify("Spanish") != cs.CLARIFY_MESSAGE
    assert cs._localized_clarify("Klingon") == cs.CLARIFY_MESSAGE


# --- Edge: ungrounded-answer detection must be language-agnostic ---
# Now that policy answers honor language, the English-marker "insufficient" guard
# could miss a translated "not enough info" reply and attach sources to it. The
# model instead emits a fixed untranslated sentinel we detect regardless of language.

def test_localized_no_results_translates_and_falls_back():
    assert cs._localized_no_results("English") == cs.NO_RESULTS_MESSAGE
    assert cs._localized_no_results("Hindi") != cs.NO_RESULTS_MESSAGE
    assert cs._localized_no_results("Klingon") == cs.NO_RESULTS_MESSAGE


def test_sentinel_detected():
    assert cs._is_no_context_sentinel(cs.NO_CONTEXT_SENTINEL)
    assert cs._is_no_context_sentinel("  " + cs.NO_CONTEXT_SENTINEL + " ")
    assert not cs._is_no_context_sentinel("You get 20 PTO days.")


def test_policy_sentinel_reply_returns_localized_no_results_without_sources(monkeypatch):
    monkeypatch.setattr(cs, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(cs, "retrieve_context",
                        lambda *a, **k: RetrievedContext("ctx", sources=[{"file": "x"}], status="ok"))
    monkeypatch.setattr(cs, "get_llm_response", lambda *a, **k: cs.NO_CONTEXT_SENTINEL)

    result = asyncio.run(cs._answer_policy_query(
        "what is the moon made of", [], ["all"], ["global"], preferences=None, language="Hindi"))
    assert result.status == "no_results"
    assert result.sources == []
    assert result.reply == cs._localized_no_results("Hindi")
    assert cs.NO_CONTEXT_SENTINEL not in result.reply


# --- Fix 4: a rephrase request must vary the answer, not copy-paste it ---

def test_is_rephrase_request_detects_reexplain_intent():
    for msg in [
        "i dont get it", "explain it in better terms", "explain the policies one by one",
        "can you simplify that", "elaborate", "in other words?", "rephrase that please",
    ]:
        assert cs._is_rephrase_request(msg), msg


def test_is_rephrase_request_passes_fresh_questions():
    for msg in [
        "what is the PTO policy", "explain the remote work policy",
        "remote work", "how many leave days do i get",
    ]:
        assert not cs._is_rephrase_request(msg), msg


def test_rephrase_followup_calls_llm_with_directive_and_temperature(monkeypatch):
    monkeypatch.setattr(cs, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(cs, "retrieve_context",
                        lambda *a, **k: RetrievedContext("ctx", sources=[{"file": "x"}], status="ok"))

    captured = {}

    def _fake_llm(*a, **k):
        captured.update(k)
        return "a clearer answer"
    monkeypatch.setattr(cs, "get_llm_response", _fake_llm)

    history = [{"role": "user", "content": "remote work?"},
               {"role": "assistant", "content": "earlier answer"}]
    result = asyncio.run(cs._answer_policy_query(
        "explain it in better terms", history, ["all"], ["global"], preferences=None))

    assert result.status == "ok"
    assert captured["extra_directive"]
    assert captured["temperature"] > 0


def test_first_message_is_not_treated_as_rephrase(monkeypatch):
    monkeypatch.setattr(cs, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(cs, "retrieve_context",
                        lambda *a, **k: RetrievedContext("ctx", sources=[{"file": "x"}], status="ok"))
    captured = {}
    monkeypatch.setattr(cs, "get_llm_response",
                        lambda *a, **k: captured.update(k) or "answer")

    # "elaborate" with NO prior assistant turn must not trigger re-explain.
    asyncio.run(cs._answer_policy_query("elaborate", [], ["all"], ["global"], preferences=None))
    assert not captured.get("extra_directive")
    assert captured.get("temperature", 0) == 0


def test_filler_message_clarifies_without_classify_or_retrieval(monkeypatch):
    monkeypatch.setattr(cs, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(cs, "_user_language", lambda uid: "English")
    monkeypatch.setattr(cs, "append_exchange", lambda *a, **k: None)

    def _boom_classify(*a, **k):
        raise AssertionError("classify must not run on filler")
    monkeypatch.setattr(cs, "classify_query", _boom_classify)

    def _boom_retrieve(*a, **k):
        raise AssertionError("retrieval must not run on filler")
    monkeypatch.setattr(cs, "retrieve_context", _boom_retrieve)

    result = asyncio.run(cs.generate_chat_reply("sess", "umm", owner_user_id=1))
    assert result.reply == cs.CLARIFY_MESSAGE
    assert result.status == "no_results"
    assert result.sources == []


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
