"""core/tools/grant_access.py
--------------------------
The THIRD write tool. Materializes an approved onboarding Case as a set of access
grants for the caller. Same guarantees as submit_leave / create_jira_issue:
(1) identity is the server-built Principal — never an arg (the registry strips
reserved keys); (2) idempotent by `case_id` — a duplicate approve-click or a graph
retry grants once; (3) registered ONLY in the graph's post-approval `provision`
node, never in a read/specialist registry, so no LLM can select it.

ONE deliberate difference from the other two write tools: connector exceptions are
NOT caught here. TransientWriteError / PermanentWriteError propagate to the graph,
which is the only layer allowed to classify a failure and decide retry-vs-stop.
Swallowing them into ToolResult(status="error") would collapse the taxonomy this
slice exists to build."""
from backend.core.access import AccessProvisioner
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal


class GrantAccessTool:
    name = "grant_access"
    description = (
        "Grant an approved onboarding Case's access bundle to the caller. "
        "Post-approval write only. Identity is supplied by the server."
    )
    parameters = {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["case_id", "tools"],
    }
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, provisioner: AccessProvisioner) -> None:
        self._provisioner = provisioner

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        granted = self._provisioner.grant(principal, args["case_id"], args["tools"])
        return ToolResult(
            status="ok",
            data={"grant_id": granted["grant_id"], "tools": granted["tools"]},
            summary=f"Access provisioned ({len(granted['tools'])} tools).",
        )
