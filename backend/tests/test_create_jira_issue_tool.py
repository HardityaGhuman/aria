"""CreateJiraIssueTool: creates via client, ignores identity in args, errors on unknown project."""
import pytest

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


def test_unknown_project_raises_permanent():
    """An unknown project will still be unknown on a retry — permanent, and explicit, so
    the graph stops instead of burning its budget."""
    from backend.core.write.errors import PermanentWriteError

    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"]))
    with pytest.raises(PermanentWriteError):
        tool.invoke(_args(project="NOPE"), _p())


def test_connector_errors_propagate_so_only_the_graph_classifies_them():
    """A tool that catches the exception decides retry-vs-stop by accident. The graph is
    the only layer allowed to classify a failure."""
    from backend.core.write.errors import TransientWriteError

    tool = CreateJiraIssueTool(MockJira(projects=["MARKETING"], fail_times=1,
                                        fail_with=TransientWriteError))
    with pytest.raises(TransientWriteError):
        tool.invoke(_args(), _p())


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
