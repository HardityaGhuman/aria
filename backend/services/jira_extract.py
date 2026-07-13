"""services/jira_extract.py
------------------------
The ONLY LLM call in the entire Jira Case: turn free text into a typed
{project, issue_type, summary, description}. Everything downstream is deterministic.
Reuses the existing native function-calling path (`select_tool_call` -> `_invoke`
funnel: retry/timeout/tracing) by forcing one extraction "tool", so no new LLM
entrypoint is introduced. The `llm_call` seam keeps this unit-testable without the
network.

Validate-or-repair is deliberately low-risk here: `project` is passed through VERBATIM
(a stated project is never redirected; an undetermined one is left empty, which the
route resolves to `unroutable` — no silent cross-team routing). `issue_type` is snapped
to the canonical allowlist (else `Task`); `summary` is repaired from the raw text if the
model returned blank; `description` defaults to empty."""
from dataclasses import dataclass

from backend.core.config import AGENT_READ_MODEL, JIRA_ALLOWED_ISSUE_TYPES

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_work_request",
        "description": (
            "Record a work request parsed from the user's message. `project` is the "
            "owning team (e.g. MARKETING, DESIGN, FINANCE, IT, OFFICE); leave it empty "
            "if the message does not name or clearly imply one. `issue_type` is the "
            "request category. `summary` is a short title; `description` is the detail."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "issue_type": {"type": "string"},
                "summary": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["project", "issue_type", "summary", "description"],
        },
    },
}


@dataclass
class JiraExtractError(Exception):
    message: str


def _default_llm_call(raw_text: str) -> dict:
    """Force the extraction tool on the small router model and return its args.
    Isolated so tests inject a stub instead of hitting the network."""
    from backend.core.llm import select_tool_call
    selection = select_tool_call(
        user_message=raw_text,
        tool_specs=[_EXTRACT_TOOL],
        model=AGENT_READ_MODEL,
        tool_choice={"type": "function", "function": {"name": "record_work_request"}},
    )
    if not selection.calls:
        raise JiraExtractError("model returned no extraction")
    return selection.calls[0]["args"]


def _snap_issue_type(raw: str) -> str:
    """Canonical allowlist match (case-insensitive), else 'Task'."""
    candidate = (raw or "").strip()
    for allowed in JIRA_ALLOWED_ISSUE_TYPES:
        if candidate.lower() == allowed.lower():
            return allowed
    return "Task"


def extract_jira_fields(raw_text: str, *, llm_call=None) -> dict:
    call = llm_call or _default_llm_call
    result = call(raw_text)
    if "summary" not in result:
        raise JiraExtractError("extraction missing field 'summary'")

    project = (result.get("project") or "").strip()           # verbatim, may be empty
    issue_type = _snap_issue_type(result.get("issue_type", ""))
    summary = (result.get("summary") or "").strip()
    if not summary:                                            # repair: fall back to raw text
        summary = raw_text.strip()[:200] or "Work request"
    description = result.get("description") or ""

    return {"project": project, "issue_type": issue_type,
            "summary": summary, "description": description}
