"""Kill-switch wiring: /agents/onboarding + /admin/onboarding routes appear in the
OpenAPI paths iff ONBOARDING_AGENT_ENABLED. Off => docs/api/openapi.json is unchanged."""
import importlib


def _paths_with_flag(monkeypatch, enabled: bool):
    monkeypatch.setenv("JWT_SECRET", "dummy")
    monkeypatch.setenv("ONBOARDING_AGENT_ENABLED", "true" if enabled else "false")
    import backend.core.config as config
    importlib.reload(config)
    import backend.main as main
    importlib.reload(main)
    return set(main.app.openapi()["paths"].keys())


def test_routes_absent_when_disabled(monkeypatch):
    paths = _paths_with_flag(monkeypatch, enabled=False)
    assert "/agents/onboarding" not in paths
    assert "/admin/onboarding/dead-letter" not in paths


def test_routes_present_when_enabled(monkeypatch):
    paths = _paths_with_flag(monkeypatch, enabled=True)
    assert "/agents/onboarding" in paths
    assert "/agents/onboarding/{case_id}" in paths
    assert "/agents/onboarding/{case_id}/decision" in paths
    assert "/admin/onboarding/dead-letter" in paths
    assert "/admin/onboarding/cases/{case_id}/replay" in paths
    assert "/admin/onboarding/breaker/reset" in paths
