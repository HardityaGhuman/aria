"""services/leave_extract.py
--------------------------
The ONLY LLM call in the entire leave Case: turn a free-text /leave message into a
typed {start_date, end_date, reason}. Everything downstream is deterministic. The
model's output is never trusted raw — dates must parse as ISO or we raise (validate-
or-repair). It reuses the existing native function-calling path (``select_tool_call``
-> ``_invoke`` funnel: retry/timeout/tracing) by forcing one extraction "tool", so no
new LLM entrypoint is introduced. The ``llm_call`` seam keeps this unit-testable
without the network and lets the graph inject the real gateway call."""
from dataclasses import dataclass
from datetime import date

from backend.core.config import AGENT_READ_MODEL  # small router model does extraction

_EXTRACT_TOOL = {
    "type": "function",
    "function": {
        "name": "record_leave_request",
        "description": (
            "Record the leave request's dates and reason parsed from the user's "
            "message. Dates MUST be ISO YYYY-MM-DD. Use the current or next "
            "occurrence of a bare month/day. If no reason is stated, use 'unspecified'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["start_date", "end_date", "reason"],
        },
    },
}


@dataclass
class LeaveExtractError(Exception):
    message: str


def _default_llm_call(raw_text: str) -> dict:
    """Force the extraction tool on the small router model and return its args.
    Isolated so tests inject a stub instead of hitting the network."""
    from backend.core.llm import select_tool_call
    selection = select_tool_call(
        user_message=raw_text,
        tool_specs=[_EXTRACT_TOOL],
        model=AGENT_READ_MODEL,
        tool_choice={"type": "function", "function": {"name": "record_leave_request"}},
    )
    if not selection.calls:
        raise LeaveExtractError("model returned no extraction")
    return selection.calls[0]["args"]


def extract_leave_fields(raw_text: str, *, llm_call=None) -> dict:
    call = llm_call or _default_llm_call
    result = call(raw_text)
    for field in ("start_date", "end_date", "reason"):
        if field not in result:
            raise LeaveExtractError(f"extraction missing field '{field}'")
    for field in ("start_date", "end_date"):
        try:
            date.fromisoformat(result[field])
        except (ValueError, TypeError) as exc:
            raise LeaveExtractError(f"extraction returned non-ISO {field}: {result.get(field)!r}") from exc
    return {"start_date": result["start_date"], "end_date": result["end_date"], "reason": result["reason"]}
