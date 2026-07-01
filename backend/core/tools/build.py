"""core/tools/build.py
---------------------
The single place the live tool registry is assembled — wires each real tool to its
backend (leave_balance → MockHRIS). chat_service pulls a cached instance from here so
the app and tests agree on the tool set. Swapping MockHRIS for a SheetsHRISClient
later is a one-line change confined to this file."""
from backend.core.hris.mock import MockHRIS
from backend.core.tools.leave_balance import LeaveBalanceTool
from backend.core.tools.registry import ToolRegistry


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(LeaveBalanceTool(MockHRIS()))
    return registry
