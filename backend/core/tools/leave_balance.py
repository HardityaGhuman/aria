"""core/tools/leave_balance.py
-----------------------------
The first real read tool: the caller's remaining leave, read live from the HRIS.

Thin adapter by design — it touches only HRISClient and does NO retrieval (the
policy citation is fused later by the grounded answer model). Identity is the
Principal; an email/user_id in args never reaches here (the registry strips reserved
keys) and is ignored regardless. Fused with a PTO policy citation upstream."""
from backend.core.hris import HRISClient
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal


class LeaveBalanceTool:
    name = "leave_balance"
    description = (
        "Look up the CALLER'S OWN remaining paid-leave (PTO) balance — how many "
        "leave days they have left. Use when the user asks about their own leave "
        "balance, days remaining, or how much PTO they have. Takes no arguments; "
        "the caller's identity is supplied by the server."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, hris: HRISClient) -> None:
        self._hris = hris

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        balance = self._hris.get_balance(principal)
        if balance is None:
            return ToolResult(
                status="ok",
                data={"remaining": None},
                summary="No HRIS leave record found for this employee.",
            )
        return ToolResult(
            status="ok",
            data=balance,
            summary=(
                f"{balance['remaining']} leave days remaining "
                f"({balance['total']} total, {balance['used']} used)."
            ),
        )
