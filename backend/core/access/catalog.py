"""core/access/catalog.py
----------------------
What a new hire of each role normally gets, and the complete set of things that
can be granted at all. PURE PYTHON — this is the security boundary, not a prompt.

The LLM's only job in this Case is to map free text onto KEYS in these dicts. It
never invents a tool name that reaches the connector: an unknown key survives
extraction, is rejected by the deterministic validator BEFORE any approval is
requested, and so never reaches `provision`. Two layers, both deterministic."""

# The allowlist: tool key -> human description (shown to the approving manager).
TOOL_CATALOG: dict[str, str] = {
    "github": "Source control",
    "jira": "Work tracking",
    "staging-db": "Staging database (read-only)",
    "figma": "Design files",
    "slack": "Chat",
    "datadog": "Observability dashboards",
    "notion": "Internal docs",
}

# What a new hire of this role normally gets. This is the agent's KNOWLEDGE — the
# reason the hire doesn't have to know what to ask for.
ROLE_BUNDLES: dict[str, list[str]] = {
    "backend-eng": ["github", "jira", "staging-db", "slack"],
    "frontend-eng": ["github", "jira", "figma", "slack"],
    "designer": ["figma", "slack"],
    "analyst": ["jira", "slack"],
}


def resolve_tools(role: str, extra_tools: list[str]) -> list[str]:
    """Bundle ∪ extras, deduped and sorted (stable => a stable idempotency key).

    Raises KeyError on an unknown role: callers MUST validate first. Deliberately
    strict — silently returning [] for a typo'd role would provision nothing and
    tell no one."""
    return sorted(set(ROLE_BUNDLES[role]) | {t.strip() for t in extra_tools if t.strip()})
