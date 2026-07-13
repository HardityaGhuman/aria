"""Kill-switch wiring: /agents/jira routes appear in the OpenAPI paths iff
JIRA_AGENT_ENABLED. Reloads main under each flag value."""
import importlib


def _paths_with_flag(monkeypatch, enabled: bool):
    monkeypatch.setenv("JWT_SECRET", "dummy")
    monkeypatch.setenv("JIRA_AGENT_ENABLED", "true" if enabled else "false")
    import backend.core.config as config
    importlib.reload(config)
    import backend.main as main
    importlib.reload(main)
    return set(main.app.openapi()["paths"].keys())


def test_routes_absent_when_disabled(monkeypatch):
    paths = _paths_with_flag(monkeypatch, enabled=False)
    assert "/agents/jira" not in paths


def test_routes_present_when_enabled(monkeypatch):
    paths = _paths_with_flag(monkeypatch, enabled=True)
    assert "/agents/jira" in paths
    assert "/agents/jira/{case_id}/decision" in paths
