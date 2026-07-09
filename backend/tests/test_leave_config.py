import importlib


def test_leave_agent_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LEAVE_AGENT_ENABLED", raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.LEAVE_AGENT_ENABLED is False


def test_leave_agent_enabled_when_truthy(monkeypatch):
    monkeypatch.setenv("LEAVE_AGENT_ENABLED", "true")
    import backend.core.config as config
    importlib.reload(config)
    assert config.LEAVE_AGENT_ENABLED is True


def test_validator_bounds_have_defaults(monkeypatch):
    for var in ("LEAVE_MAX_CONSECUTIVE_DAYS", "LEAVE_MIN_NOTICE_DAYS", "LEAVE_BLACKOUT_DATES"):
        monkeypatch.delenv(var, raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.LEAVE_MAX_CONSECUTIVE_DAYS == 20
    assert config.LEAVE_MIN_NOTICE_DAYS == 3
    assert config.LEAVE_BLACKOUT_DATES == []


def test_checkpoint_dsn_falls_back_to_database_url(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DSN", raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.LANGGRAPH_CHECKPOINT_DSN == config.DATABASE_URL
