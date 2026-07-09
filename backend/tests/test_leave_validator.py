from datetime import date

from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal
from backend.services.leave_validator import validate_leave, compute_days


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


TODAY = date(2026, 8, 1)


def test_compute_days_inclusive():
    assert compute_days("2026-08-12", "2026-08-14") == 3


def test_happy_path_ok():
    r = validate_leave(_p(), MockHRIS(), "2026-08-12", "2026-08-14", today=TODAY)
    assert r.ok and r.days == 3 and r.reason is None


def test_reversed_dates_fail():
    r = validate_leave(_p(), MockHRIS(), "2026-08-14", "2026-08-12", today=TODAY)
    assert not r.ok and "date" in r.reason.lower()


def test_min_notice_fail():
    r = validate_leave(_p(), MockHRIS(), "2026-08-02", "2026-08-02", today=TODAY)  # 1 day notice < 3
    assert not r.ok and "notice" in r.reason.lower()


def test_max_consecutive_fail():
    r = validate_leave(_p(), MockHRIS(), "2026-08-12", "2026-09-30", today=TODAY)  # > 20 days
    assert not r.ok and "consecutive" in r.reason.lower()


def test_insufficient_balance_fail():
    hris = MockHRIS(seed={"employee@gsvh.test": {"total_pto": 5, "pto_used": 0, "region": "us", "manager_email": "manager@gsvh.test"}})
    r = validate_leave(_p(), hris, "2026-08-12", "2026-08-18", today=TODAY)  # 7 days > 5 remaining
    assert not r.ok and "balance" in r.reason.lower()


def test_no_hris_record_fails_closed():
    r = validate_leave(_p("nobody@gsvh.test"), MockHRIS(), "2026-08-12", "2026-08-14", today=TODAY)
    assert not r.ok and "record" in r.reason.lower()
