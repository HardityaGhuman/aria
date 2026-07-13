"""services/onboarding_validator.py
--------------------------------
The deterministic pre-approval gate. No LLM: membership checks against the pure-Python
catalog, in a fixed order, short-circuiting on the first failure with a short, user-safe
reason. Same request => same verdict, always, and fully unit-testable.

This is the second of the two layers that keep a hallucinated tool name away from the
connector (the first is that the catalog is Python, not a prompt). The model may emit
"prod-root"; it never gets past this function, and the graph therefore never reaches
`request_approval` — an invalid request does not even waste the manager's attention.

Fail-closed: an unknown role or an off-catalog tool is a rejection, never a repair."""
from dataclasses import dataclass, field

from backend.core.access.catalog import ROLE_BUNDLES, TOOL_CATALOG, resolve_tools


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None
    tools: list[str] = field(default_factory=list)


def validate_onboarding(role: str, extra_tools: list[str]) -> ValidationResult:
    role = (role or "").strip()
    extras = [t.strip() for t in (extra_tools or []) if t and t.strip()]

    if role not in ROLE_BUNDLES:
        return ValidationResult(False, f"unknown role: {role}")
    for tool in extras:
        if tool not in TOOL_CATALOG:
            return ValidationResult(False, f"unknown tool: {tool}")

    tools = resolve_tools(role, extras)
    if not tools:
        return ValidationResult(False, "no tools to provision")
    return ValidationResult(True, None, tools)
