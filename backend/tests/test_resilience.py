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


# --- _run_blocking maps failures to the uniform envelope (no leak) ---

import asyncio

import backend.services.chat_service as cs


def test_run_blocking_wraps_unknown_failure_as_llm_error_without_leak():
    def _boom():
        raise RuntimeError("groq exploded: secret-internal-detail")

    with pytest.raises(AppError) as exc:
        asyncio.run(cs._run_blocking(_boom, timeout_detail="t"))
    assert exc.value.code == "llm_error"
    assert exc.value.status_code == 502
    assert "secret-internal-detail" not in exc.value.message  # raw cause not leaked


def test_run_blocking_passes_through_existing_app_error():
    def _already_enveloped():
        raise AppError("llm_error", "already enveloped", status_code=502)

    with pytest.raises(AppError) as exc:
        asyncio.run(cs._run_blocking(_already_enveloped, timeout_detail="t"))
    assert exc.value.code == "llm_error"


def test_run_blocking_timeout_maps_to_llm_timeout(monkeypatch):
    monkeypatch.setattr(cs, "LLM_TIMEOUT_SECONDS", -10)  # wait_for timeout = -5 → immediate

    def _slow():
        import time
        time.sleep(0.2)
        return 1

    with pytest.raises(AppError) as exc:
        asyncio.run(cs._run_blocking(_slow, timeout_detail="model timed out"))
    assert exc.value.code == "llm_timeout"
    assert exc.value.status_code == 504
    assert exc.value.message == "model timed out"
