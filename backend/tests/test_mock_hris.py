"""The mock HRIS is the bounded system of record for leave (v1). It is keyed by
email — the stable identity the Principal carries — and returns None for anyone it
has no record of, so a caller without an HRIS row degrades cleanly."""
from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal


def _p(email):
    return Principal(user_id=1, email=email, role="employee", region="us")


def test_known_employee_returns_remaining():
    hris = MockHRIS()
    bal = hris.get_balance(_p("employee@gsvh.test"))
    assert bal is not None
    assert bal["remaining"] == bal["total"] - bal["used"]
    assert bal["remaining"] >= 0


def test_unknown_email_returns_none():
    assert MockHRIS().get_balance(_p("nobody@gsvh.test")) is None


def test_none_email_returns_none():
    assert MockHRIS().get_balance(_p(None)) is None


def test_seeded_users_all_present():
    hris = MockHRIS()
    for email in ("hr@gsvh.test", "manager@gsvh.test", "employee@gsvh.test", "employee2@gsvh.test"):
        assert hris.get_balance(_p(email)) is not None
