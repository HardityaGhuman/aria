"""core/calendar/mock.py
----------------------
A local, seeded, in-memory Calendar — the v1 read-only source for the team's
out-of-office view. Chosen over a live Google Calendar so a demo runs with no
service-account setup, no network/quota on the critical path, and is fast +
unit-testable. It is a distinct bounded module the middleware calls, NOT OOO tables
in the chat schema — the ownership line holds. Mirrors core/hris/mock.py.

Each seed row models a real calendar absence: `start`..`until` (return date), plus
private fields (`email`, `reason`) that a real calendar would carry. The visibility
projection here proves the boundary: only `_DISPLAY_FIELDS` leave `whos_out` — the
private fields never reach a ToolResult and so can never reach the prompt. A real
Google adapter must project the same way (and sanitize the display text — S2)."""
from datetime import date

from backend.core.tools.principal import Principal

# The only fields allowed out of the calendar boundary. Everything else on a row is
# private and dropped — enforcing "display-safe fields only" at the source.
_DISPLAY_FIELDS = ("name", "until")

# Seeded out-of-office rows. `start`/`until` bound each absence (until = return date,
# i.e. first day back); `email`/`reason` are deliberately-present PRIVATE fields the
# projection must strip. Trusted seed data — a real adapter reading attacker-
# influenceable calendar text must sanitize before it reaches a summary (S2).
_SEED = [
    {"name": "Priya Sharma", "start": "2026-07-01", "until": "2026-07-10",
     "email": "priya@corp.test", "reason": "vacation"},
    {"name": "Marcus Bell", "start": "2026-07-06", "until": "2026-07-08",
     "email": "marcus@corp.test", "reason": "sick leave"},
    {"name": "Elena Rossi", "start": "2026-07-12", "until": "2026-07-14",
     "email": "elena@corp.test", "reason": "conference"},
]


class MockCalendar:
    def __init__(self, seed: list[dict] | None = None) -> None:
        # Copy each row so a caller mutating the returned view can't bleed into the
        # seed (and one test can't corrupt another).
        source = _SEED if seed is None else seed
        self._rows = [dict(row) for row in source]

    def whos_out(self, principal: Principal, start_date: date, end_date: date) -> list[dict]:
        """Absences overlapping the inclusive window, projected to display-safe fields.

        An absence [row.start, row.until) overlaps [start_date, end_date] iff it began
        on/before the window ends AND the person had not yet returned when the window
        began: `row.start <= end_date and row.until > start_date`."""
        out = []
        for row in self._rows:
            r_start = date.fromisoformat(row["start"])
            r_until = date.fromisoformat(row["until"])
            if r_start <= end_date and r_until > start_date:
                out.append({field: row[field] for field in _DISPLAY_FIELDS})
        return out
