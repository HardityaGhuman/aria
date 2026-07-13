"""extract_jira_fields: passes project verbatim, snaps issue_type to allowlist,
repairs blank summary, defaults description. LLM stubbed via llm_call seam."""
import pytest

from backend.services.jira_extract import JiraExtractError, extract_jira_fields


def _stub(**over):
    base = {"project": "MARKETING", "issue_type": "Task", "summary": "Landing page", "description": "desc"}
    base.update(over)
    return lambda raw: base


def test_happy_extract():
    out = extract_jira_fields("marketing needs a landing page", llm_call=_stub())
    assert out == {"project": "MARKETING", "issue_type": "Task",
                   "summary": "Landing page", "description": "desc"}


def test_project_passed_through_verbatim():
    out = extract_jira_fields("x", llm_call=_stub(project="WEIRD_TEAM"))
    assert out["project"] == "WEIRD_TEAM"  # never redirected/defaulted


def test_undetermined_project_left_empty():
    out = extract_jira_fields("x", llm_call=_stub(project=""))
    assert out["project"] == ""  # downstream route -> unroutable


def test_unknown_issue_type_snaps_to_task():
    out = extract_jira_fields("x", llm_call=_stub(issue_type="Emergency"))
    assert out["issue_type"] == "Task"


def test_blank_summary_repaired_from_raw_text():
    out = extract_jira_fields("please procure a laptop", llm_call=_stub(summary="   "))
    assert out["summary"].strip() != ""


def test_missing_summary_key_raises():
    with pytest.raises(JiraExtractError):
        extract_jira_fields("x", llm_call=lambda raw: {"project": "MARKETING"})
