"""validate_jira: pure allowlist + length checks, fail-closed, fixed order."""
from backend.services.jira_validator import validate_jira


def _fields(**over):
    base = {"project": "MARKETING", "issue_type": "Task", "summary": "Landing page", "description": "desc"}
    base.update(over)
    return base


def test_valid_fields_pass():
    assert validate_jira(_fields()).ok is True


def test_project_not_in_allowlist_fails():
    r = validate_jira(_fields(project="SECRET"))
    assert r.ok is False and "project" in r.reason.lower()


def test_issue_type_not_in_allowlist_fails():
    assert validate_jira(_fields(issue_type="Bug-Blocker")).ok is False


def test_empty_summary_fails():
    assert validate_jira(_fields(summary="   ")).ok is False


def test_summary_too_long_fails():
    assert validate_jira(_fields(summary="x" * 5000)).ok is False


def test_description_too_long_fails():
    assert validate_jira(_fields(description="x" * 100000)).ok is False
