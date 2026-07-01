"""Registry invariants: RBAC filtering (min_role), identity-from-Principal (a tool
never trusts a user_id in args), and invalid args are never executed."""
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry
from backend.core.tools.stub import EchoTool, ManagerReadTool, StubWriteTool

EMPLOYEE = Principal(user_id=1, email="e@x.test", role="employee", region="us")
MANAGER = Principal(user_id=2, email="m@x.test", role="manager", region="us")


def _registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(ManagerReadTool())
    reg.register(StubWriteTool())
    return reg


def test_specs_for_employee_hide_manager_tool():
    names = {s["function"]["name"] for s in _registry().specs_for(EMPLOYEE)}
    assert "echo" in names
    assert "manager_read" not in names  # min_role manager — invisible to employee


def test_specs_for_manager_include_manager_tool():
    names = {s["function"]["name"] for s in _registry().specs_for(MANAGER)}
    assert "manager_read" in names


def test_spec_shape_is_openai_function_calling():
    spec = next(s for s in _registry().specs_for(EMPLOYEE) if s["function"]["name"] == "echo")
    assert spec["type"] == "function"
    assert "parameters" in spec["function"]
    # No identity field is ever advertised in a tool schema.
    assert "user_id" not in spec["function"]["parameters"].get("properties", {})


def test_invoke_binds_identity_to_principal_not_args():
    # Adversarial: the model asks to act as user 999. The tool must act on the
    # Principal (user 1), ignoring the injected user_id entirely.
    result = _registry().invoke("echo", {"text": "hi", "user_id": 999}, EMPLOYEE)
    assert result.status == "ok"
    assert result.data["acting_user_id"] == 1


def test_invoke_rejects_tool_above_callers_role():
    # An employee naming a manager-only tool is denied even if they guess the name.
    result = _registry().invoke("manager_read", {}, EMPLOYEE)
    assert result.status == "error"


def test_invoke_rejects_invalid_args_without_executing():
    # echo requires 'text' (string). A wrong-typed arg is an error, not an execution.
    result = _registry().invoke("echo", {"text": 123}, EMPLOYEE)
    assert result.status == "error"
    assert "text" in (result.error or "")


def test_invoke_unknown_tool_is_error():
    assert _registry().invoke("no_such_tool", {}, EMPLOYEE).status == "error"
