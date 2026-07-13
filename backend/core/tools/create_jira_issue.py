"""core/tools/create_jira_issue.py
--------------------------------
The SECOND write tool. Materializes an approved work request as a Jira issue for the
caller. Same three guarantees as submit_leave: (1) identity is the server Principal —
never an arg (the registry strips reserved keys); (2) idempotent by `case_id` — a
duplicate approve-click / graph re-entry creates once; (3) registered ONLY in the
graph's post-approval `create` node, never in a read/specialist registry. No LLM can
select it."""
from backend.core.jira import JiraClient
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal


class CreateJiraIssueTool:
    name = "create_jira_issue"
    description = (
        "Create an approved work request as a Jira issue for the caller. "
        "Post-approval write only. Identity is supplied by the server."
    )
    parameters = {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "project": {"type": "string"},
            "issue_type": {"type": "string"},
            "summary": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["case_id", "project", "issue_type", "summary", "description"],
    }
    requires_confirmation = False
    min_role = "employee"

    def __init__(self, jira: JiraClient) -> None:
        self._jira = jira

    def invoke(self, args: dict, principal: Principal) -> ToolResult:
        try:
            created = self._jira.create_issue(
                principal, args["case_id"], args["project"], args["issue_type"],
                args["summary"], args["description"],
            )
        except KeyError as exc:
            return ToolResult(status="error", error=f"could not create issue: {exc}")
        return ToolResult(
            status="ok",
            data={"issue_key": created["issue_key"], "url": created["url"]},
            summary=f"Work request created ({created['issue_key']}).",
        )
