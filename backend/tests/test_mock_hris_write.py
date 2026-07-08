import pytest
from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal


def _p(email, region="us"):
    return Principal(user_id=1, email=email, role="employee", region=region)


def test_manager_email_lookup():
    hris = MockHRIS()
    assert hris.manager_email(_p("employee@gsvh.test")) == "manager@gsvh.test"
    assert hris.manager_email(_p("hr@gsvh.test")) is None
    assert hris.manager_email(_p("nobody@gsvh.test")) is None


def test_submit_leave_decrements_balance_once():
    hris = MockHRIS()
    before = hris.get_balance(_p("employee@gsvh.test"))["remaining"]
    res = hris.submit_leave(_p("employee@gsvh.test"), "case-1", "2026-08-12", "2026-08-14", 3)
    assert res["confirmation_id"]
    after = hris.get_balance(_p("employee@gsvh.test"))["remaining"]
    assert after == before - 3


def test_submit_leave_idempotent_by_case_id():
    hris = MockHRIS()
    before = hris.get_balance(_p("employee@gsvh.test"))["remaining"]
    a = hris.submit_leave(_p("employee@gsvh.test"), "case-1", "2026-08-12", "2026-08-14", 3)
    b = hris.submit_leave(_p("employee@gsvh.test"), "case-1", "2026-08-12", "2026-08-14", 3)
    assert a["confirmation_id"] == b["confirmation_id"]
    after = hris.get_balance(_p("employee@gsvh.test"))["remaining"]
    assert after == before - 3  # decremented exactly once


def test_submit_leave_unknown_employee_raises():
    with pytest.raises(KeyError):
        MockHRIS().submit_leave(_p("nobody@gsvh.test"), "case-9", "2026-08-12", "2026-08-14", 3)
