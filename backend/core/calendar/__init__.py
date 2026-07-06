"""core/calendar — the Calendar boundary. The team's out-of-office truth lives
behind the CalendarClient interface; the chat/RAG app only ever touches this seam,
never a concrete backend. v1 backend is a local seeded mock (mock.py). A Google
CalendarClient adapter is a later drop-in that satisfies the same Protocol.

Ownership line: nothing about OOO is persisted in our Postgres — a tool reads a
transient team view through this interface and forgets it. Mirrors core/hris."""
from typing import Protocol

from backend.core.tools.principal import Principal


class CalendarClient(Protocol):
    def whos_out(self, principal: Principal) -> list[dict]:
        """Colleagues currently out of office, each as ``{"name", "until"}``.
        Read-only team view. Identity comes from the Principal, never LLM args."""
        ...
