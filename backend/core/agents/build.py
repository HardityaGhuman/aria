"""core/agents/build.py
---------------------
The single place specialists are assembled with their SCOPED registries. Each read
specialist gets ONLY its own tool (canonical §7 / rescope): HR-agent → leave_balance
(→ MockHRIS); Calendar-agent → whos_out (→ MockCalendar); Policy-agent → empty
registry (pure RAG). No specialist ever receives a superset — that per-specialist
isolation is the guarantee the supervisor + plan validator rely on: a calendar
request can never reach the HRIS read, and an HR request can never reach the calendar
read. Swapping a mock for a real adapter is a one-line change here."""
from backend.core.agents.specialist import Specialist
from backend.core.calendar.mock import MockCalendar
from backend.core.hris.mock import MockHRIS
from backend.core.tools.leave_balance import LeaveBalanceTool
from backend.core.tools.registry import ToolRegistry
from backend.core.tools.whos_out import WhosOutTool


def _hr_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(LeaveBalanceTool(MockHRIS()))
    return reg


def _calendar_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(WhosOutTool(MockCalendar()))
    return reg


def build_specialists() -> list[Specialist]:
    return [
        Specialist(
            name="hr-agent",
            description=(
                "Handles the caller's own HR data — their leave balance / PTO "
                "remaining — fused with policy citations."
            ),
            min_role="employee",
            registry=_hr_registry(),
            uses_tools=True,
        ),
        Specialist(
            name="calendar-agent",
            description=(
                "Answers who on the team is out of office over a bounded date range, "
                "from display-safe calendar data."
            ),
            min_role="employee",
            registry=_calendar_registry(),
            uses_tools=True,
        ),
        Specialist(
            name="policy-agent",
            description="Answers any company-policy question from the document corpus (pure RAG).",
            min_role="employee",
            registry=ToolRegistry(),
            uses_tools=False,
        ),
    ]
