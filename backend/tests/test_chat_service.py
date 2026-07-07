"""
tests/test_chat_service.py
--------------------------
Unit tests for backend.services.chat_service.
"""
import asyncio

import backend.services.chat_service as cs
import backend.services.read_pipeline as rp


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


# NOTE: the grounded-answer behaviors formerly tested here through
# `_answer_policy_query` (sentinel → localized no_results, blocked confidential,
# rephrase directive, tool-note folding) moved with that logic into the shared
# read pipeline. They are now covered in tests/test_read_pipeline.py.


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


def test_filler_message_clarifies_without_classify_or_retrieval(monkeypatch):
    # The filler short-circuit lives in the pipeline now; patch its deps there. The
    # transport still owns persistence, so append_exchange is patched on cs.
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(cs, "append_exchange", lambda *a, **k: None)

    def _boom_classify(*a, **k):
        raise AssertionError("classify must not run on filler")
    monkeypatch.setattr(rp, "classify_query", _boom_classify)

    def _boom_retrieve(*a, **k):
        raise AssertionError("retrieval must not run on filler")
    monkeypatch.setattr(rp, "retrieve_context", _boom_retrieve)

    result = asyncio.run(cs.generate_chat_reply("sess", "umm", owner_user_id=1))
    assert result.reply == cs.CLARIFY_MESSAGE
    assert result.status == "no_results"
    assert result.sources == []
