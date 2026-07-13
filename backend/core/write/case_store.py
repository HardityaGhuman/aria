"""core/write/case_store.py
------------------------
The Case engine, shared by every write agent. A Case is the unit of work (not the chat
message): it has an id, a status, an owner, an approver, an idempotency key, an
append-only audit trail, and a lifecycle that only moves in legal directions.

The lifecycle is DERIVED from the spec, never hand-written per agent. That is the whole
point: an agent that forgets `dead_letter` cannot exist, so the reliability the write
boundary depends on is structural rather than remembered. Only the success word is the
agent's to choose ("booked" / "created" / "provisioned"), because the audit log should
speak the business's language.

Each agent keeps its own table and its own typed business columns — the duplication that
mattered was the ENGINE (status guard + audit-in-the-same-transaction + idempotency),
not the schema. Column names reaching SQL come from this frozen, app-owned dataclass;
values are always parameterized.
"""
from dataclasses import dataclass

from backend.core import db

# The shared control statuses. Every agent has exactly these, plus one success word.
CONTROL_STATUSES = frozenset({
    "draft", "pending_approval", "approved", "denied_policy", "denied_manager",
    "unroutable", "write_failed", "dead_letter",
})


@dataclass
class WriteCaseError(Exception):
    message: str


@dataclass(frozen=True)
class CaseSpec:
    agent: str                        # "leave" | "jira" | "onboarding" — URL + DLQ discriminator
    table: str
    audit_table: str
    success_status: str               # the ONE agent-chosen word
    result_column: str                # confirmation_id | issue_key | grant_id
    summary_columns: tuple[str, ...]  # business columns the list/read surface projects

    def __post_init__(self) -> None:
        if self.success_status in CONTROL_STATUSES:
            raise ValueError(
                f"success_status {self.success_status!r} collides with a control status"
            )

    def legal_transitions(self) -> dict[str, set[str]]:
        """Exhaustive, one-way lifecycle. `dead_letter` is the ONLY non-terminal failure:
        a human can replay it, and idempotency-by-case_id makes that safe."""
        return {
            "draft": {"pending_approval", "denied_policy", "unroutable"},
            "pending_approval": {"approved", "denied_manager"},
            "approved": {self.success_status, "write_failed", "dead_letter"},
            "dead_letter": {"approved"},          # replay re-enters the write
            "denied_policy": set(),
            "denied_manager": set(),
            "unroutable": set(),
            "write_failed": set(),
            self.success_status: set(),
        }

    def statuses(self) -> tuple[str, ...]:
        """For the table's CHECK constraint."""
        return tuple(sorted(CONTROL_STATUSES | {self.success_status}))


def _connect():
    return db.pooled(lambda: WriteCaseError("Could not connect to PostgreSQL for write cases."))
