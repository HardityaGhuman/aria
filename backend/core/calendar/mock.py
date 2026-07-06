"""core/calendar/mock.py
----------------------
A local, seeded, in-memory Calendar — the v1 read-only source for the team's
out-of-office view. Chosen over a live Google Calendar so a demo runs with no
service-account setup, no network/quota on the critical path, and is fast +
unit-testable. It is a distinct bounded module the middleware calls, NOT OOO tables
in the chat schema — the ownership line holds. Mirrors core/hris/mock.py."""
from backend.core.tools.principal import Principal

# Seeded out-of-office rows. Names are the display value shown back; `until` is the
# return date. Trusted seed data — a real adapter reading attacker-influenceable
# calendar text must sanitize before it reaches a ToolResult.summary (S2 invariant).
_SEED = [
    {"name": "Priya Sharma", "until": "2026-07-10"},
    {"name": "Marcus Bell", "until": "2026-07-08"},
    {"name": "Elena Rossi", "until": "2026-07-14"},
]


class MockCalendar:
    def __init__(self, seed: list[dict] | None = None) -> None:
        # Copy each row so a caller mutating the returned view can't bleed into the
        # seed (and one test can't corrupt another).
        source = _SEED if seed is None else seed
        self._rows = [dict(row) for row in source]

    def whos_out(self, principal: Principal) -> list[dict]:
        return [dict(row) for row in self._rows]
