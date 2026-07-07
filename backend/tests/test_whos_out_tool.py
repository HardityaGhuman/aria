"""whos_out is a thin CalendarClient adapter: a read-only, bounded team out-of-office
view. §5.3 hardening — it now takes an optional start_date/end_date/timezone window,
validates it deterministically, and rejects a bad range BEFORE touching the calendar
backend. Its summary stays TEMPLATE-built from typed display-safe fields (name +
return date), never raw external text spliced in with authority (S2). Identity is
still the Principal; injected identity args are ignored."""
from datetime import datetime, timezone

from backend.core.tools.principal import Principal
from backend.core.tools.whos_out import WhosOutTool

ALICE = Principal(user_id=1, email="alice@x.test", role="employee", region="us")

# A fixed clock so 'today'-dependent behavior (default window, timezone) is
# deterministic. 02:00 UTC on 2026-07-06 — deliberately early so a west-of-UTC zone
# still reads the *previous* calendar day.
_NOW = datetime(2026, 7, 6, 2, 0, tzinfo=timezone.utc)


class _SpyCalendar:
    """Records the (start, end) it was called with, so tests can prove the tool
    forwards a validated window — and that a rejected range never calls it at all."""
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def whos_out(self, principal, start_date, end_date):
        self.calls.append((start_date, end_date))
        return [dict(r) for r in self.rows]


def _tool(rows=None, now=_NOW):
    return WhosOutTool(_SpyCalendar(rows), now=lambda: now)


# --- window resolution ------------------------------------------------------

def test_default_window_starts_today_when_dates_omitted():
    cal = _SpyCalendar()
    WhosOutTool(cal, now=lambda: _NOW).invoke({}, ALICE)
    (start, end), = cal.calls
    assert start.isoformat() == "2026-07-06"
    assert end > start  # a multi-day default window, not a single day


def test_explicit_range_is_parsed_and_forwarded():
    cal = _SpyCalendar()
    res = WhosOutTool(cal, now=lambda: _NOW).invoke(
        {"start_date": "2026-07-01", "end_date": "2026-07-05"}, ALICE)
    assert res.status == "ok"
    (start, end), = cal.calls
    assert start.isoformat() == "2026-07-01"
    assert end.isoformat() == "2026-07-05"


def test_timezone_resolves_today_to_the_local_calendar_day():
    cal = _SpyCalendar()
    # 02:00 UTC is still 2026-07-05 in Los Angeles (UTC-7) — the default window must
    # start on the *local* day, not the UTC day.
    WhosOutTool(cal, now=lambda: _NOW).invoke({"timezone": "America/Los_Angeles"}, ALICE)
    (start, _end), = cal.calls
    assert start.isoformat() == "2026-07-05"


# --- validation: rejected BEFORE the backend is touched ---------------------

def test_start_after_end_is_rejected_pre_exec():
    cal = _SpyCalendar()
    res = WhosOutTool(cal, now=lambda: _NOW).invoke(
        {"start_date": "2026-07-10", "end_date": "2026-07-01"}, ALICE)
    assert res.status == "error"
    assert cal.calls == []  # never reached the calendar


def test_window_over_maximum_is_rejected_pre_exec():
    cal = _SpyCalendar()
    res = WhosOutTool(cal, now=lambda: _NOW).invoke(
        {"start_date": "2026-01-01", "end_date": "2026-12-31"}, ALICE)
    assert res.status == "error"
    assert cal.calls == []


def test_malformed_date_is_rejected_pre_exec():
    cal = _SpyCalendar()
    res = WhosOutTool(cal, now=lambda: _NOW).invoke({"start_date": "2026/07/10"}, ALICE)
    assert res.status == "error"
    assert cal.calls == []


def test_unknown_timezone_is_rejected_pre_exec():
    cal = _SpyCalendar()
    res = WhosOutTool(cal, now=lambda: _NOW).invoke({"timezone": "Mars/Phobos"}, ALICE)
    assert res.status == "error"
    assert cal.calls == []


# --- result shape -----------------------------------------------------------

def test_lists_people_out_with_return_dates():
    res = _tool([
        {"name": "Priya", "until": "2026-07-10"},
        {"name": "Marcus", "until": "2026-07-08"},
    ]).invoke({"start_date": "2026-07-01", "end_date": "2026-07-15"}, ALICE)
    assert res.status == "ok"
    assert res.data["out"][0]["name"] == "Priya"
    assert "Priya" in res.summary and "2026-07-10" in res.summary
    assert "Marcus" in res.summary


def test_nobody_out_is_a_clean_result():
    res = _tool([]).invoke({}, ALICE)
    assert res.status == "ok"
    assert res.data["out"] == []
    assert "no one" in res.summary.lower()


def test_ignores_injected_identity_args():
    # Adversarial args must not change what the read returns.
    res = _tool([{"name": "Priya", "until": "2026-07-10"}]).invoke(
        {"email": "boss@x.test", "name": "hacked"}, ALICE)
    assert res.data["out"][0]["name"] == "Priya"


def test_tool_metadata_declares_the_date_window_params():
    t = _tool()
    assert t.name == "whos_out"
    assert t.requires_confirmation is False
    assert t.min_role == "employee"
    props = t.parameters["properties"]
    assert set(props) == {"start_date", "end_date", "timezone"}
    assert t.parameters.get("required", []) == []  # all optional — defaults to today
