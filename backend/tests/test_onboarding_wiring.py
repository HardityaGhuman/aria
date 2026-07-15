"""Kill-switch wiring: the onboarding routes — and the cross-agent /agents/cases +
/admin/write surfaces — appear in the OpenAPI paths iff a write agent is enabled. The
admin DLQ/breaker routes are no longer onboarding's private property; they moved to
/admin/write once leave and jira could dead-letter too."""
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
    assert "/admin/onboarding/dead-letter" not in paths      # retired surface, never returns


def test_routes_present_when_enabled(monkeypatch):
    paths = _paths_with_flag(monkeypatch, enabled=True)
    assert "/agents/onboarding" in paths
    assert "/agents/onboarding/{case_id}" in paths
    assert "/agents/onboarding/{case_id}/decision" in paths
    # The Case surfaces are agent-agnostic now: one list, one DLQ, one breaker board.
    assert "/agents/cases" in paths
    assert "/agents/cases/{agent}/{case_id}" in paths
    assert "/admin/write/dead-letter" in paths
    assert "/admin/write/cases/{agent}/{case_id}/replay" in paths
    assert "/admin/write/breakers" in paths
    assert "/admin/write/breaker/{connector}/reset" in paths


def test_an_enabled_agent_registers_itself_with_its_spec_graph_and_replay(monkeypatch):
    """The registry is what makes /agents/cases and /admin/write agent-agnostic. An agent
    whose kill switch is off is never registered — so its Cases are invisible to the
    cross-agent surfaces, which is what "off" must mean."""
    from backend.core.jira_case import JIRA_SPEC
    from backend.core.leave_case import LEAVE_SPEC
    from backend.core.onboarding_case import ONBOARDING_SPEC
    from backend.services import write_agents

    write_agents.reset()
    import backend.main as main
    monkeypatch.setattr(main, "LEAVE_AGENT_ENABLED", True)
    monkeypatch.setattr(main, "JIRA_AGENT_ENABLED", True)
    monkeypatch.setattr(main, "ONBOARDING_AGENT_ENABLED", False)
    main._register_write_agents(leave_graph=object(), jira_graph=object(),
                                onboarding_graph=object())

    assert set(write_agents.AGENTS) == {"leave", "jira"}     # onboarding is OFF
    assert write_agents.get("leave").spec is LEAVE_SPEC
    assert write_agents.get("jira").spec is JIRA_SPEC
    assert callable(write_agents.get("leave").replay)
    assert ONBOARDING_SPEC not in write_agents.specs()
    write_agents.reset()
