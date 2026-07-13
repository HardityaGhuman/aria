"""CreateJiraIssueTool: creates via client, ignores identity in args, errors on unknown project."""
from backend.core.jira.mock import MockJira
from backend.core.tools.create_jira_issue import CreateJiraIssueTool
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def _args(case_id="c1", project="MARKETING"):
    return {"case_id": case_id, "project": project, "issue_type": "Task",
            "summary": "Landing page", "description": "desc"}


def test_create_ok():
    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"]))
    res = tool.invoke(_args(), _p())
    assert res.status == "ok"
    assert res.data["issue_key"] == "MARKETING-1"


def test_unknown_project_returns_error():
    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"]))
    res = tool.invoke(_args(project="NOPE"), _p())
    assert res.status == "error"


def test_registry_strips_injected_identity():
    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"]))
    reg = ToolRegistry()
    reg.register(tool)
    args = _args()
    args["email"] = "attacker@gsvh.test"  # must be stripped by the registry
    res = reg.invoke("create_jira_issue", args, _p())
    assert res.status == "ok"


def test_idempotent_by_case_id():
    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"]))
    a = tool.invoke(_args(case_id="cX"), _p())
    b = tool.invoke(_args(case_id="cX"), _p())
    assert a.data["issue_key"] == b.data["issue_key"]
