"""Jira agent config: kill switch defaults off; allowlists + approver map parse."""
import importlib


def test_agent_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JIRA_AGENT_ENABLED", raising=False)
    import backend.core.config as config
    importlib.reload(config)
    assert config.JIRA_AGENT_ENABLED is False


def test_enabled_when_true(monkeypatch):
    monkeypatch.setenv("JIRA_AGENT_ENABLED", "true")
    import backend.core.config as config
    importlib.reload(config)
    assert config.JIRA_AGENT_ENABLED is True


def test_approver_map_parses_json(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_APPROVERS", '{"MARKETING": "cmo@gsvh.test"}')
    import backend.core.config as config
    importlib.reload(config)
    assert config.JIRA_PROJECT_APPROVERS == {"MARKETING": "cmo@gsvh.test"}


def test_approver_map_malformed_falls_back_to_empty(monkeypatch):
    monkeypatch.setenv("JIRA_PROJECT_APPROVERS", "not json")
    import backend.core.config as config
    importlib.reload(config)
    assert config.JIRA_PROJECT_APPROVERS == {}


def test_allowlists_split_and_strip(monkeypatch):
    monkeypatch.setenv("JIRA_ALLOWED_PROJECTS", " MARKETING , DESIGN ")
    import backend.core.config as config
    importlib.reload(config)
    assert config.JIRA_ALLOWED_PROJECTS == ["MARKETING", "DESIGN"]
