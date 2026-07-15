"""core/tools/submit_leave.py
---------------------------
The FIRST write tool. Books leave in the HRIS for the caller. Three guarantees:
(1) identity is the server Principal — never an arg (registry strips reserved keys,
and the tool passes ``principal`` straight to the HRIS, ignoring any arg email);
(2) idempotent by ``case_id`` — a duplicate approve-click / graph re-entry books once;
(3) registered ONLY in the write-agent registry and invoked ONLY from the graph
``book`` node, which runs only after a verified manager approval. No read specialist
sees it; no LLM can select it."""
from backend.core.hris import HRISClient
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.core.write.errors import PermanentWriteError


class SubmitLeaveTool:
    name = "submit_leave"
    description = (
        "Book approved leave for the caller in the HRIS. Post-approval write only. "
        "Identity is supplied by the server."
    )
    parameters = {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "days": {"type": "integer"},
        },
        "required": ["case_id", "start_date", "end_date", "days"],
    }
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, hris: HRISClient) -> None:
        self._hris = hris

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        # Connector errors are NOT caught here: Transient/PermanentWriteError propagate to
        # the graph, the only layer allowed to classify a failure and decide retry-vs-stop.
        # A tool that swallows the exception into ToolResult(status="error") flattens a
        # timeout and a "no such record" into the same string, and the decision is then
        # made by accident.
        try:
            booked = self._hris.submit_leave(
                principal, args["case_id"], args["start_date"], args["end_date"], args["days"],
            )
        except KeyError as exc:
            raise PermanentWriteError(f"no HRIS record for caller: {exc}") from exc
        # The HRIS's echo of what it booked is forwarded verbatim: the graph verifies the
        # booked dates against the dates the manager approved, and it can only do that if
        # the connector's answer reaches it.
        return ToolResult(
            status="ok",
            data={"confirmation_id": booked["confirmation_id"], "remaining": booked["remaining"],
                  "start_date": booked.get("start_date"), "end_date": booked.get("end_date"),
                  "days": booked.get("days")},
            summary=f"Leave booked (confirmation {booked['confirmation_id']}).",
        )
