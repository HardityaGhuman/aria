"""core/tools/stub.py
--------------------
Harmless stub tools used to prove the loop + registry invariants without any
external I/O. NOT registered in any live registry — Unit 2 adds the first real
tool (leave_balance). Tests build their own registry from these."""
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal


class EchoTool:
    name = "echo"
    description = "Echo back the given text. Test tool with no side effect."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_confirmation = False
    min_role = "employee"

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        # acting_user_id proves identity binds to the Principal, not to args.
        return ToolResult(
            status="ok",
            data={"echo": args.get("text"), "acting_user_id": principal.user_id},
            summary=f"echoed: {args.get('text')}",
        )


class ManagerReadTool:
    name = "manager_read"
    description = "A manager-only read stub, for RBAC tests."
    parameters = {"type": "object", "properties": {}, "required": []}
    requires_confirmation = False
    min_role = "manager"

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        return ToolResult(status="ok", data={"ok": True})


class StubWriteTool:
    name = "stub_write"
    description = "A write stub gated by confirmation, for gate tests. No real effect."
    parameters = {
        "type": "object",
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
    }
    requires_confirmation = True
    min_role = "employee"

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        # Records the mutation only in-memory so a test can assert it ran (or didn't).
        return ToolResult(
            status="ok",
            data={"written_amount": args.get("amount"), "acting_user_id": principal.user_id},
            summary=f"wrote amount={args.get('amount')}",
        )
