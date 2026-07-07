"""core/tools/whos_out.py
-----------------------
A read tool: which colleagues are out of office over a bounded date window, read live
from the Calendar. Team-wide view (no per-caller filtering) — everyone may see who is
away, so min_role is employee.

Visibility policy (§5.3): the read is bounded and display-safe by construction.
  - Bounded: an optional start_date/end_date window, defaulting to a short window
    from today; the window may not exceed MAX_WINDOW_DAYS, so the model can't
    enumerate the far-future calendar.
  - Display-safe: only name + return date ever surface (the CalendarClient projects
    away private fields — attendee email, absence reason). No event details leak.

Thin adapter by design — it touches only CalendarClient and does NO retrieval. The
window is the only thing the model controls; identity is the Principal, and injected
identity args are ignored (the registry strips reserved keys). A malformed or
out-of-bounds window is rejected HERE, before the backend is called. The summary is
TEMPLATE-built from typed fields (name + return date) so no raw external text enters
the prompt with authority (S2 invariant)."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.core.calendar import CalendarClient
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal

# The default window is "today plus the next few days" (this week) when the caller
# gives no dates; the hard cap bounds how far ahead any single read may look.
DEFAULT_WINDOW_DAYS = 7
MAX_WINDOW_DAYS = 92  # ~one quarter


def _parse_iso_date(value: str) -> date | None:
    """Strict ISO YYYY-MM-DD only; returns None on anything else (e.g. 2026/07/10)."""
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class WhosOutTool:
    name = "whos_out"
    description = (
        "List which colleagues are out of office / on leave over a date range and "
        "until when. Use when the user asks who is away, who is out, who is on leave "
        "or vacation, or about team availability. Optional start_date/end_date "
        "(YYYY-MM-DD) bound the window; omit them for the current week."
    )
    parameters = {
        "type": "object",
        "properties": {
            "start_date": {
                "type": "string",
                "description": "Window start, ISO date YYYY-MM-DD (inclusive). Omit for today.",
            },
            "end_date": {
                "type": "string",
                "description": (
                    "Window end, ISO date YYYY-MM-DD (inclusive). Omit for a short "
                    "default window from the start."
                ),
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone (e.g. 'America/New_York') used to resolve 'today'. Defaults to UTC.",
            },
        },
        "required": [],
    }
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, calendar: CalendarClient, now=None) -> None:
        self._calendar = calendar
        # Injected clock (returns an aware datetime) so 'today' is deterministic in
        # tests. NOT a tool argument — the model can't set it.
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _reject(self, reason: str) -> ToolResult:
        # A validation failure: return an error WITHOUT calling the backend. The
        # control layer maps this to on_invalid_calendar_range (§4.3) at wire time.
        return ToolResult(
            status="error",
            error=reason,
            summary="I couldn't read the calendar for that date range.",
        )

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        # 1. Resolve the timezone (only used to anchor a default window on 'today').
        tz_name = args.get("timezone")
        if tz_name:
            try:
                zone = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                return self._reject(f"unknown timezone '{tz_name}'")
        else:
            zone = timezone.utc
        today = self._now().astimezone(zone).date()

        # 2. Resolve the window. Missing dates default; present dates must parse.
        raw_start = args.get("start_date")
        if raw_start is not None:
            start = _parse_iso_date(raw_start)
            if start is None:
                return self._reject(f"start_date '{raw_start}' is not an ISO date (YYYY-MM-DD)")
        else:
            start = today

        raw_end = args.get("end_date")
        if raw_end is not None:
            end = _parse_iso_date(raw_end)
            if end is None:
                return self._reject(f"end_date '{raw_end}' is not an ISO date (YYYY-MM-DD)")
        else:
            end = start + timedelta(days=DEFAULT_WINDOW_DAYS - 1)

        # 3. Validate the range BEFORE touching the backend.
        if start > end:
            return self._reject("start_date is after end_date")
        if (end - start).days + 1 > MAX_WINDOW_DAYS:
            return self._reject(f"date window exceeds the {MAX_WINDOW_DAYS}-day maximum")

        # 4. Read the display-safe view for the validated window.
        people = self._calendar.whos_out(principal, start, end)
        if not people:
            return ToolResult(
                status="ok",
                data={"out": []},
                summary="No one is marked out of office in that period.",
            )
        parts = [f"{p['name']} (until {p['until']})" for p in people]
        return ToolResult(
            status="ok",
            data={"out": people},
            summary=f"{len(people)} out of office: " + ", ".join(parts) + ".",
        )
