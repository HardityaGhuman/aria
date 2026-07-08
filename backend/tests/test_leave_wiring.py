"""Kill-switch wiring: the leave routers are mounted only when LEAVE_AGENT_ENABLED.
Reloads config + main with the env toggled so the module-scope mount is exercised."""
import importlib


def _reload_main(monkeypatch, enabled: bool):
    monkeypatch.setenv("LEAVE_AGENT_ENABLED", "true" if enabled else "false")
    import backend.core.config as config
    importlib.reload(config)
    import backend.main as main
    importlib.reload(main)
    return main


def test_leave_routers_mounted_when_enabled(monkeypatch):
    main = _reload_main(monkeypatch, enabled=True)
    paths = main.app.openapi()["paths"].keys()
    assert "/agents/leave" in paths
    assert "/auth/slack/start" in paths


def test_leave_routers_absent_when_disabled(monkeypatch):
    main = _reload_main(monkeypatch, enabled=False)
    paths = main.app.openapi()["paths"].keys()
    assert "/agents/leave" not in paths
    assert "/auth/slack/start" not in paths
    # Restore default-disabled config for any later test importing config fresh.
    importlib.reload(main)
