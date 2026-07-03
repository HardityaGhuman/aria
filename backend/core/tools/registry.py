"""core/tools/registry.py
------------------------
The tool registry: RBAC-filtered spec exposure and a guarded invoke path.

Two invariants live here. (1) specs_for(principal) returns only tools the caller's
role may see (min_role) — the model is never shown a tool it can't use. (2) invoke
re-checks RBAC, strips reserved identity keys from args, and validates the args
against the tool's schema BEFORE execution — an out-of-role or schema-invalid call
is never run. Identity always comes from the Principal, never from args."""
from backend.core.logging import get_logger
from backend.core.tools.base import Tool, ToolResult, role_allows, validate_args
from backend.core.tools.principal import Principal

logger = get_logger(__name__)

# Arg keys a tool must never accept — identity is Principal-only. Silently dropped
# before validation so an LLM-injected user_id can't reach a tool.
_RESERVED_IDENTITY_KEYS = ("user_id", "email", "principal", "role")


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs_for(self, principal: Principal) -> list[dict]:
        """OpenAI-format function specs for the tools this principal may use."""
        specs = []
        for tool in self._tools.values():
            if not role_allows(principal.role, tool.min_role):
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            })
        return specs

    def invoke(self, name: str, args: dict, principal: Principal) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(status="error", error=f"unknown tool '{name}'")
        if not role_allows(principal.role, tool.min_role):
            return ToolResult(status="error", error=f"not authorized for tool '{name}'")

        clean = {k: v for k, v in (args or {}).items() if k not in _RESERVED_IDENTITY_KEYS}
        errors = validate_args(tool.parameters, clean)
        if errors:
            return ToolResult(status="error", error="; ".join(errors))

        try:
            return tool.invoke(clean, principal)
        except Exception:
            # A tool backend raising (network, quota, bug) must never escape as an
            # exception that kills the chat request. The error string is generic on
            # purpose: ToolResult.error can be fed back into a prompt, and a raw
            # backend message is external text that must not gain prompt authority.
            logger.exception("Tool '%s' raised during invoke", name)
            return ToolResult(status="error", error=f"tool '{name}' failed to execute")
