"""core/jira/
----------
The Jira seam. `JiraClient` is the interface the write agent talks to; the issue
tracker is the sole system of record for the issue — we never persist issue bodies
here (mirrors the HRIS ownership line). v1 backend is `MockJira`; a real outbound
JiraClient (with connectors-page OAuth) is a later slice."""
from typing import Protocol, runtime_checkable

from backend.core.tools.principal import Principal


@runtime_checkable
class JiraClient(Protocol):
    def create_issue(
        self, principal: Principal, case_id: str, project: str,
        issue_type: str, summary: str, description: str,
    ) -> dict:
        """Create one work-request issue. Returns {issue_key, url}.
        Idempotent by case_id. Unknown project raises KeyError."""
        ...
