"""core/agents/build.py
---------------------
The single place specialists are assembled with their SCOPED registries. HR-agent
gets ONLY leave_balance (→ MockHRIS); Policy-agent gets an empty registry (pure
RAG). No specialist ever receives a superset — that is the isolation guarantee the
supervisor relies on. Swapping MockHRIS for a real adapter is a one-line change here."""
from backend.core.agents.specialist import Specialist
from backend.core.hris.mock import MockHRIS
from backend.core.tools.leave_balance import LeaveBalanceTool
from backend.core.tools.registry import ToolRegistry


def _hr_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(LeaveBalanceTool(MockHRIS()))
    return reg


def build_specialists() -> list[Specialist]:
    return [
        Specialist(
            name="hr-agent",
            description=(
                "Handles the caller's own HR data and requests — leave balance, "
                "PTO, and (later) leave requests — fused with policy citations."
            ),
            min_role="employee",
            registry=_hr_registry(),
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
