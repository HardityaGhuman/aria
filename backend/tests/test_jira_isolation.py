"""The write tool `create_jira_issue` must never leak into a read/specialist registry.
Same guarantee the leave slice holds for `submit_leave`."""
from backend.core.agents.build import build_specialists


def test_create_jira_issue_absent_from_every_specialist_registry():
    for spec in build_specialists():
        assert spec.registry.get("create_jira_issue") is None, (
            f"create_jira_issue leaked into specialist {spec!r}")
