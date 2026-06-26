"""Tests for LLM resilience helpers (Task 5): context truncation + retry/backoff."""
import pytest

from backend.core import llm
from backend.core.errors import AppError


# --- context truncation ---

def test_short_context_is_unchanged():
    text = "Employees get 20 days of PTO."
    assert llm.truncate_to_token_budget(text, max_tokens=1000) == text


def test_long_context_is_truncated_under_budget():
    text = "word " * 5000  # well over a tiny budget
    budget = 50
    out = llm.truncate_to_token_budget(text, max_tokens=budget)
    assert len(out) < len(text)
    assert llm.count_tokens([{"role": "user", "content": out}]) <= budget


# --- retry / backoff ---

def test_transient_error_retries_then_raises_app_error():
    calls = {"n": 0}

    def _always_transient():
        calls["n"] += 1
        raise RuntimeError("Connection reset by peer")

    with pytest.raises(AppError) as exc:
        llm.call_with_retry(_always_transient, retries=2, base_delay=0)

    assert exc.value.code == "llm_error"
    assert calls["n"] == 3  # 1 initial + 2 retries


def test_non_transient_error_fails_fast_without_retrying():
    calls = {"n": 0}

    def _hard_error():
        calls["n"] += 1
        raise ValueError("bad request: invalid model")

    with pytest.raises(AppError) as exc:
        llm.call_with_retry(_hard_error, retries=3, base_delay=0)

    assert exc.value.code == "llm_error"
    assert calls["n"] == 1  # not retried


def test_succeeds_after_a_transient_failure():
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("503 overloaded")
        return "ok"

    assert llm.call_with_retry(_flaky, retries=2, base_delay=0) == "ok"
    assert calls["n"] == 2
