"""core/tools/whos_out.py
-----------------------
A read tool: which colleagues are currently out of office, and until when, read
live from the Calendar. Team-wide view (no per-caller filtering) — everyone may see
who is away, so min_role is employee.

Thin adapter by design — it touches only CalendarClient and does NO retrieval. Takes
no arguments; injected args are ignored (the registry strips reserved keys and the
tool never reads them). The summary is TEMPLATE-built from typed fields (name +
return date) so it carries no raw external text into the prompt (S2 invariant)."""
from backend.core.calendar import CalendarClient
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal


class WhosOutTool:
    name = "whos_out"
    description = (
        "List which colleagues are currently out of office / on leave and until "
        "when. Use when the user asks who is away, who is out, who is on leave or "
        "vacation, or about team availability. Takes no arguments."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, calendar: CalendarClient) -> None:
        self._calendar = calendar

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        people = self._calendar.whos_out(principal)
        if not people:
            return ToolResult(
                status="ok",
                data={"out": []},
                summary="No one is currently marked out of office.",
            )
        parts = [f"{p['name']} (until {p['until']})" for p in people]
        return ToolResult(
            status="ok",
            data={"out": people},
            summary=f"{len(people)} out of office: " + ", ".join(parts) + ".",
        )
