"""
tests/test_llm.py
-----------------
Unit tests for prompt construction in backend.core.llm. These exercise the
deterministic message-building surface (ordering, preference reinforcement,
re-explain directive) — not the model's stochastic output.
"""
import backend.core.llm as llm


# --- Fix 1: language/length preference must be reinforced AFTER the context ---
# On the policy route the answer model sees a large English context block in the
# user turn; a preference directive only at the top (system prompt) gets
# out-competed and the model answers in English. The augmented user turn must
# restate the preference at the very end, closest to generation.

def test_augmented_message_repeats_preferences_after_context():
    msg = llm._augmented_message(
        "What is the PTO policy?",
        "Some English policy excerpt text.",
        preferences="Write the answer in Spanish.",
    )
    # The directive must appear, and appear AFTER the closing context fence so it
    # is the last thing the model reads before generating.
    assert "Write the answer in Spanish." in msg
    close_idx = msg.rindex("</policy_context_")
    assert msg.index("Write the answer in Spanish.") > close_idx


def test_augmented_message_without_preferences_has_no_trailing_reminder():
    msg = llm._augmented_message("q", "ctx")
    # Nothing after the closing fence except the fence's own tail.
    assert "preference" not in msg.lower()


# --- Fix 5: never leak the internal "the employee" framing into the answer ---
# A confused answer parroted the prompt scaffolding ("The employee's question is
# 'elaborate'...") and spoke of the user in the third person. The system prompt
# must explicitly require direct second-person address.

def test_system_prompt_carries_no_context_sentinel():
    # The prompt must instruct the model to emit the exact sentinel the service
    # detects (language-agnostic), keeping the two in sync.
    import backend.services.chat_service as cs
    assert cs.NO_CONTEXT_SENTINEL in llm.load_system_prompt()


def test_system_prompt_requires_direct_address():
    prompt = llm.load_system_prompt().lower()
    assert "the employee" in prompt  # still used in instruction text
    # but there must be an explicit output rule to address the user as "you"
    assert "third person" in prompt
    assert "you" in prompt


# --- Fix 4: a re-explain directive + non-zero temperature breaks identical output ---
# With temperature=0, "explain it better" rewrote to the same query, retrieved the
# same chunks, and produced byte-identical text. The answer path must accept an
# extra directive (placed near generation) and a temperature override.

def test_get_llm_response_threads_extra_directive_and_temperature(monkeypatch):
    captured = {}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "ok"
            message = _Msg()
        choices = [_Choice()]

    def _fake_retry(fn, purpose, **kwargs):
        captured.update(kwargs)
        captured["purpose"] = purpose
        return _Resp()

    monkeypatch.setattr(llm, "call_with_retry", _fake_retry)

    llm.get_llm_response(
        "explain it better", "ctx", [],
        extra_directive="RE-EXPLAIN SIMPLY", temperature=0.4,
    )
    assert captured["temperature"] == 0.4
    joined = " ".join(m["content"] for m in captured["messages"])
    assert "RE-EXPLAIN SIMPLY" in joined


# --- Streaming resilience parity: retry the initial connect like the sync path ---
# get_llm_response rides call_with_retry; stream_llm_response dialed litellm
# directly, so one transient "connection reset" failed the whole stream. Retrying
# is only safe BEFORE any token is yielded (a mid-stream retry would duplicate
# text), so the retry wraps just the initial completion call.

def test_stream_llm_response_retries_transient_connect_error(monkeypatch):
    from types import SimpleNamespace

    calls = {"n": 0}

    def fake_completion(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("Connection reset by peer")
        return iter([
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))],
                usage=None,
            )
        ])

    monkeypatch.setattr(llm, "LLM_RETRY_BASE_DELAY", 0)
    monkeypatch.setattr(llm.litellm, "completion", fake_completion)

    tokens = list(llm.stream_llm_response("q", "ctx", []))
    assert tokens == ["hi"]
    assert calls["n"] == 2  # failed once, retried, streamed


def test_get_llm_response_defaults_to_zero_temperature(monkeypatch):
    captured = {}

    class _Resp:
        class _Choice:
            class _Msg:
                content = "ok"
            message = _Msg()
        choices = [_Choice()]

    monkeypatch.setattr(llm, "call_with_retry",
                        lambda fn, purpose, **kw: captured.update(kw) or _Resp())
    llm.get_llm_response("q", "ctx", [])
    assert captured["temperature"] == 0
