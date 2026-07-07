"""The mock Calendar is the bounded read-only source for the team's out-of-office
view (v1). §5.3 hardening: it now takes a date window and returns ONLY the absences
that overlap it, projected to display-safe fields (name + return date) — private
calendar fields (email, reason, absence start) never leave the boundary. A real
Google CalendarClient is a later drop-in behind the same range-query Protocol."""
from datetime import date

from backend.core.calendar.mock import MockCalendar
from backend.core.tools.principal import Principal


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


# Seed with private fields present, so the projection is actually exercised.
_ROWS = [
    {"name": "Priya", "start": "2026-07-01", "until": "2026-07-10",
     "email": "priya@corp.test", "reason": "vacation"},
    {"name": "Elena", "start": "2026-07-12", "until": "2026-07-14",
     "email": "elena@corp.test", "reason": "conference"},
]


def test_filters_to_the_requested_window_overlap():
    cal = MockCalendar(seed=_ROWS)
    # A single-day window on 2026-07-13 overlaps Elena's absence only (Priya returned
    # 07-10; her absence does not reach 07-13).
    out = cal.whos_out(_p(), date(2026, 7, 13), date(2026, 7, 13))
    assert [r["name"] for r in out] == ["Elena"]


def test_returns_display_safe_fields_only():
    cal = MockCalendar(seed=_ROWS)
    out = cal.whos_out(_p(), date(2026, 7, 1), date(2026, 7, 31))
    assert out, "window should overlap both seeded absences"
    for row in out:
        assert set(row) == {"name", "until"}
        assert "email" not in row and "reason" not in row and "start" not in row


def test_no_overlap_returns_empty():
    cal = MockCalendar(seed=_ROWS)
    assert cal.whos_out(_p(), date(2025, 1, 1), date(2025, 1, 31)) == []


def test_empty_seed_returns_empty_list():
    assert MockCalendar(seed=[]).whos_out(_p(), date(2026, 7, 1), date(2026, 7, 31)) == []


def test_rows_are_copied_not_shared():
    cal = MockCalendar(seed=_ROWS)
    first = cal.whos_out(_p(), date(2026, 7, 1), date(2026, 7, 31))
    first[0]["name"] = "MUTATED"
    assert cal.whos_out(_p(), date(2026, 7, 1), date(2026, 7, 31))[0]["name"] != "MUTATED"
