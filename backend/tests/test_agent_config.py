"""Agentic-layer config flags. AGENT_TOOLS_ENABLED must default OFF so the live
pipeline is unchanged until the loop is deliberately switched on."""
import importlib

import backend.core.config as config


def test_agent_tools_disabled_by_default():
    assert config.AGENT_TOOLS_ENABLED is False


def test_max_tool_steps_defaults_to_three():
    assert config.MAX_TOOL_STEPS == 3


def test_agent_read_model_defaults_to_main_model():
    # v1 rule: tool-select rides the 70B answer model; 8B read promotion is later.
    assert config.AGENT_READ_MODEL == config.MODEL_NAME


def test_agent_tools_enabled_parses_truthy(monkeypatch):
    monkeypatch.setenv("AGENT_TOOLS_ENABLED", "true")
    reloaded = importlib.reload(config)
    assert reloaded.AGENT_TOOLS_ENABLED is True
    monkeypatch.delenv("AGENT_TOOLS_ENABLED", raising=False)
    importlib.reload(config)  # restore module-global default for other tests
