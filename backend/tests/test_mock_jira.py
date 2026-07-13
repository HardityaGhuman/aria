"""MockJira: monotonic per-project keys, idempotent by case_id, unknown project raises."""
import pytest

from backend.core.jira.mock import MockJira
from backend.core.tools.principal import Principal


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def test_create_returns_key_and_url():
    jira = MockJira(projects=["MARKETING"])
    out = jira.create_issue(_p(), "c1", "MARKETING", "Work Request", "Landing page", "desc")
    assert out["issue_key"] == "MARKETING-1"
    assert out["url"].endswith("MARKETING-1")


def test_counter_is_monotonic_per_project():
    jira = MockJira(projects=["MARKETING", "DESIGN"])
    a = jira.create_issue(_p(), "c1", "MARKETING", "Task", "s", "d")
    b = jira.create_issue(_p(), "c2", "MARKETING", "Task", "s", "d")
    c = jira.create_issue(_p(), "c3", "DESIGN", "Task", "s", "d")
    assert a["issue_key"] == "MARKETING-1"
    assert b["issue_key"] == "MARKETING-2"
    assert c["issue_key"] == "DESIGN-1"


def test_idempotent_by_case_id():
    jira = MockJira(projects=["MARKETING"])
    a = jira.create_issue(_p(), "c1", "MARKETING", "Task", "s", "d")
    b = jira.create_issue(_p(), "c1", "MARKETING", "Task", "s", "d")
    assert a["issue_key"] == b["issue_key"]
    # no second issue minted
    c = jira.create_issue(_p(), "c2", "MARKETING", "Task", "s", "d")
    assert c["issue_key"] == "MARKETING-2"


def test_unknown_project_raises_keyerror():
    jira = MockJira(projects=["MARKETING"])
    with pytest.raises(KeyError):
        jira.create_issue(_p(), "c1", "NOPE", "Task", "s", "d")
