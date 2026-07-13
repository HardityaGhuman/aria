"""core/jira/mock.py
------------------
A local, seeded, in-memory issue tracker — the v1 system of record for work-request
issues. `create_issue` mints a monotonic per-project key ({PROJECT}-{n}) and is
idempotent by case_id (a retried approve-click / graph re-entry creates once). Bounded
module behind `JiraClient`; never chat tables — the ownership line holds."""
from backend.core.tools.principal import Principal

_BASE_URL = "https://jira.mock.gsvh.test/browse"


class MockJira:
    def __init__(self, projects: list[str] | None = None) -> None:
        self._projects = set(projects or ["MARKETING", "DESIGN", "FINANCE", "IT", "OFFICE"])
        self._counters: dict[str, int] = {}
        # case_id -> {"issue_key", "url"} — idempotency ledger.
        self._issues: dict[str, dict] = {}

    def create_issue(
        self, principal: Principal, case_id: str, project: str,
        issue_type: str, summary: str, description: str,
    ) -> dict:
        # Idempotent replay: same case_id -> same issue, no second key minted.
        prior = self._issues.get(case_id)
        if prior is not None:
            return dict(prior)
        if project not in self._projects:
            raise KeyError(f"unknown project {project!r}")
        n = self._counters.get(project, 0) + 1
        self._counters[project] = n
        issue_key = f"{project}-{n}"
        out = {"issue_key": issue_key, "url": f"{_BASE_URL}/{issue_key}"}
        self._issues[case_id] = out
        return dict(out)
