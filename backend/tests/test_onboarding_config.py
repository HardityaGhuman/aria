"""Onboarding + write-reliability config: safe defaults (agent OFF), env-overridable."""
import importlib


def test_agent_defaults_off(monkeypatch):
    monkeypatch.delenv("ONBOARDING_AGENT_ENABLED", raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.ONBOARDING_AGENT_ENABLED is False


def test_agent_enabled_by_env(monkeypatch):
    monkeypatch.setenv("ONBOARDING_AGENT_ENABLED", "true")
    import backend.core.config as config
    importlib.reload(config)
    assert config.ONBOARDING_AGENT_ENABLED is True


def test_write_reliability_defaults(monkeypatch):
    monkeypatch.delenv("WRITE_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("WRITE_BREAKER_THRESHOLD", raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.WRITE_MAX_ATTEMPTS == 3
    assert config.WRITE_BREAKER_THRESHOLD == 3
