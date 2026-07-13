"""The write tool `grant_access` must never leak into a read/specialist registry —
the same guarantee the leave and jira slices hold. No LLM can ever select it."""
from backend.core.agents.build import build_specialists


def test_grant_access_absent_from_every_specialist_registry():
    for spec in build_specialists():
        assert spec.registry.get("grant_access") is None, (
            f"grant_access leaked into specialist {spec!r}")
