"""core/tools — the agentic tool layer: identity (Principal), the tool registry
with RBAC, arg-schema validation, and stub tools. External-I/O tools land later.

Kept free of langchain/langgraph on purpose (see the model-allocation spec §3)."""
from backend.core.tools.base import Tool, ToolResult, role_allows, validate_args
from backend.core.tools.principal import Principal, principal_from_user
from backend.core.tools.registry import ToolRegistry

__all__ = [
    "Principal",
    "principal_from_user",
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "validate_args",
    "role_allows",
]
