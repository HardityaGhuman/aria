from backend.core.hris.mock import MockHRIS
from backend.core.tools.submit_leave import SubmitLeaveTool
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def test_submit_books_for_principal():
    tool = SubmitLeaveTool(MockHRIS())
    res = tool.invoke({"case_id": "c1", "start_date": "2026-08-12", "end_date": "2026-08-14", "days": 3}, _p())
    assert res.status == "ok"
    assert res.data["confirmation_id"]


def test_submit_ignores_identity_in_args():
    hris = MockHRIS()
    tool = SubmitLeaveTool(hris)
    # An injected email in args must be dropped by the registry and ignored by the tool.
    reg = ToolRegistry()
    reg.register(tool)
    res = reg.invoke(
        "submit_leave",
        {"case_id": "c2", "start_date": "2026-08-12", "end_date": "2026-08-14", "days": 3,
         "email": "hr@gsvh.test"},
        _p("employee@gsvh.test"),
    )
    assert res.status == "ok"
    # employee's balance moved, not hr's.
    assert hris.get_balance(_p("employee@gsvh.test"))["used"] == 11
    assert hris.get_balance(_p("hr@gsvh.test"))["used"] == 4


def test_submit_idempotent_via_case_id():
    tool = SubmitLeaveTool(MockHRIS())
    a = tool.invoke({"case_id": "c3", "start_date": "2026-08-12", "end_date": "2026-08-14", "days": 3}, _p())
    b = tool.invoke({"case_id": "c3", "start_date": "2026-08-12", "end_date": "2026-08-14", "days": 3}, _p())
    assert a.data["confirmation_id"] == b.data["confirmation_id"]


def test_submit_unknown_employee_returns_error():
    tool = SubmitLeaveTool(MockHRIS())
    res = tool.invoke({"case_id": "c4", "start_date": "2026-08-12", "end_date": "2026-08-14", "days": 3},
                      _p("nobody@gsvh.test"))
    assert res.status == "error"
