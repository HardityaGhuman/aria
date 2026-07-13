"""The deterministic gate. Runs BEFORE any approval is requested, so an invalid
request never reaches the manager, let alone the connector. Pure: no LLM, no HRIS,
no DB — same input, same verdict, always."""
from backend.services.onboarding_validator import validate_onboarding


def test_known_role_no_extras_resolves_the_bundle():
    r = validate_onboarding("backend-eng", [])
    assert r.ok is True
    assert r.reason is None
    assert r.tools == ["github", "jira", "slack", "staging-db"]


def test_extras_union_with_the_bundle():
    r = validate_onboarding("backend-eng", ["figma"])
    assert r.ok is True
    assert "figma" in r.tools
    assert "github" in r.tools


def test_unknown_role_rejected():
    r = validate_onboarding("astronaut", [])
    assert r.ok is False
    assert r.reason == "unknown role: astronaut"
    assert r.tools == []


def test_off_catalog_tool_rejected():
    # The model hallucinated a tool. It survived extraction; it dies HERE.
    r = validate_onboarding("designer", ["prod-root"])
    assert r.ok is False
    assert r.reason == "unknown tool: prod-root"
    assert r.tools == []


def test_empty_role_rejected():
    r = validate_onboarding("", [])
    assert r.ok is False
    assert r.reason.startswith("unknown role")
