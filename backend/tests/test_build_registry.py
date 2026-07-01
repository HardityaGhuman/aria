"""The default registry wires the real tools to the mock HRIS. One place the app
pulls its tool set from, so tests and chat_service agree on what's registered."""
from backend.core.tools.build import build_default_registry
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def test_registry_exposes_leave_balance_to_employee():
    reg = build_default_registry()
    names = {s["function"]["name"] for s in reg.specs_for(EMPLOYEE)}
    assert "leave_balance" in names


def test_leave_balance_invokes_against_mock_hris():
    reg = build_default_registry()
    result = reg.invoke("leave_balance", {}, EMPLOYEE)
    assert result.status == "ok"
    assert result.data["remaining"] == 20 - 8  # seeded employee@gsvh.test row
