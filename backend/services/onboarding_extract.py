"""services/onboarding_extract.py
------------------------------
The ONLY LLM call in the entire onboarding Case: turn free text into a typed
{role, extra_tools}. Everything downstream is deterministic Python.

Reuses the existing native function-calling path (`select_tool_call` -> `_invoke`
funnel: retry/timeout/tracing) by forcing one extraction "tool", so no new LLM
entrypoint is introduced. The `llm_call` seam keeps this unit-testable without the
network.

NO validate-or-repair on the values. `role` and `extra_tools` are passed through
verbatim (normalized to lowercase/stripped only). Snapping a hallucinated tool to
the nearest catalog key would be a silent, unauditable rewrite of what the user
asked for — and worse, could GRANT something nobody requested. Unknown values are
the validator's job to REJECT. The model proposes; Python disposes."""
from dataclasses import dataclass

from backend.core.access.catalog import ROLE_BUNDLES, TOOL_CATALOG
from backend.core.config import AGENT_READ_MODEL

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_onboarding_request",
        "description": (
            "Record a new hire's onboarding request parsed from their message. "
            f"`role` MUST be one of: {', '.join(sorted(ROLE_BUNDLES))} — leave it empty "
            "if the message names no recognisable role. `extra_tools` are ADDITIONAL "
            f"tools explicitly asked for, each one of: {', '.join(sorted(TOOL_CATALOG))}. "
            "Do not include tools the role already implies; do not invent names."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "extra_tools": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["role", "extra_tools"],
        },
    },
}


@dataclass
class OnboardingExtractError(Exception):
    message: str


def _default_llm_call(raw_text: str) -> dict:
    """Force the extraction tool on the small router model and return its args.
    Isolated so tests inject a stub instead of hitting the network."""
    from backend.core.llm import select_tool_call
    selection = select_tool_call(
        user_message=raw_text,
        tool_specs=[_EXTRACT_TOOL],
        model=AGENT_READ_MODEL,
        tool_choice={"type": "function", "function": {"name": "record_onboarding_request"}},
    )
    if not selection.calls:
        raise OnboardingExtractError("model returned no extraction")
    return selection.calls[0]["args"]


def extract_onboarding_fields(raw_text: str, *, llm_call=None) -> dict:
    call = llm_call or _default_llm_call
    result = call(raw_text)
    if "role" not in result:
        raise OnboardingExtractError("extraction missing field 'role'")

    role = (result.get("role") or "").strip().lower()
    extras = [str(t).strip().lower() for t in (result.get("extra_tools") or []) if str(t).strip()]
    return {"role": role, "extra_tools": extras}
