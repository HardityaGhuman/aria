"""core/hris — the HRIS boundary. Leave truth lives behind the HRISClient
interface; the chat/RAG app only ever touches this seam, never a concrete backend.
v1 backend is a local seeded mock (mock.py). A SheetsHRISClient adapter is a later
drop-in that satisfies the same Protocol without any demo depending on it.

Ownership line: nothing about leave is persisted in our Postgres — a tool reads a
transient value through this interface and forgets it."""
from typing import Protocol

from backend.core.tools.principal import Principal


class HRISClient(Protocol):
    def get_balance(self, principal: Principal) -> dict | None:
        """Remaining/total/used leave for the caller, or None if no HRIS record.
        Identity is the Principal — never an LLM-supplied argument."""
        ...

    def manager_email(self, principal: Principal) -> str | None:
        """The caller's approving manager's email, or None if unrouted."""
        ...

    def submit_leave(
        self, principal: Principal, case_id: str, start_date: str, end_date: str, days: int
    ) -> dict:
        """Book leave for the caller. Idempotent by ``case_id``. Returns
        ``{"confirmation_id": str, "remaining": int}``. Raises KeyError for an
        employee the HRIS has no record of."""
        ...
