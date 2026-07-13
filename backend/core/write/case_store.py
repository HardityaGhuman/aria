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

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

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


def create_case(spec: CaseSpec, employee_email: str, approver_email: str | None,
                idempotency_key: str, **business) -> dict:
    """Insert a draft Case + its 'drafted' audit row in ONE transaction. On an
    idempotency_key collision return the existing row — no second Case, no second audit
    row, no second graph run."""
    cols = ["employee_email", "approver_email", "idempotency_key", *business.keys()]
    values = [employee_email, approver_email, idempotency_key, *business.values()]
    placeholders = ", ".join(["%s"] * len(cols))
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    f"INSERT INTO {spec.table} ({', '.join(cols)}) "
                    f"VALUES ({placeholders}) RETURNING *",
                    tuple(values),
                )
                row = dict(cursor.fetchone())
                cursor.execute(
                    f"INSERT INTO {spec.audit_table} (case_id, event, actor_id, detail) "
                    f"VALUES (%s, %s, %s, %s)",
                    (row["case_id"], "drafted", employee_email, spec.agent),
                )
                return row
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                cursor.execute(
                    f"SELECT * FROM {spec.table} WHERE idempotency_key = %s", (idempotency_key,)
                )
                return dict(cursor.fetchone())


def get_case(spec: CaseSpec, case_id: str) -> dict | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"SELECT * FROM {spec.table} WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


def get_by_idempotency_key(spec: CaseSpec, idempotency_key: str) -> dict | None:
    """The route calls this BEFORE it extracts: a duplicate submit costs neither an LLM
    call nor a second graph invocation on a thread already parked at the approval gate."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT * FROM {spec.table} WHERE idempotency_key = %s", (idempotency_key,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None


def transition(spec: CaseSpec, case_id: str, new_status: str, actor_id: str, detail: str,
               **result) -> dict:
    """Move a Case iff the move is legal, writing the audit row in the SAME transaction so
    status and audit can never diverge. `**result` carries agent-specific columns
    (confirmation_id / issue_key / grant_id) plus the control columns attempt /
    failure_reason; each is COALESCEd, so a later transition never erases an earlier value.
    Raises WriteCaseError on an illegal or unknown-case move."""
    allowed_result_cols = {spec.result_column, "attempt", "failure_reason"}
    unknown = set(result) - allowed_result_cols
    if unknown:
        raise WriteCaseError(f"unknown result column(s) for {spec.agent}: {sorted(unknown)}")

    sets = ", ".join(f"{col} = COALESCE(%s, {col})" for col in result)
    set_clause = f"status = %s, {sets}, updated_at = now()" if result else \
                 "status = %s, updated_at = now()"

    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT status FROM {spec.table} WHERE case_id = %s FOR UPDATE", (case_id,)
            )
            current = cursor.fetchone()
            if current is None:
                raise WriteCaseError(f"no such case {case_id}")
            cur_status = current["status"]
            if new_status not in spec.legal_transitions().get(cur_status, set()):
                raise WriteCaseError(f"illegal transition {cur_status} -> {new_status}")
            cursor.execute(
                f"UPDATE {spec.table} SET {set_clause} WHERE case_id = %s RETURNING *",
                (new_status, *result.values(), case_id),
            )
            row = dict(cursor.fetchone())
            cursor.execute(
                f"INSERT INTO {spec.audit_table} (case_id, event, actor_id, detail) "
                f"VALUES (%s, %s, %s, %s)",
                (case_id, new_status, actor_id, detail),
            )
            return row


def list_audit(spec: CaseSpec, case_id: str) -> list[dict]:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"SELECT event, actor_id, detail, created_at FROM {spec.audit_table} "
                f"WHERE case_id = %s ORDER BY id",
                (case_id,),
            )
            return [dict(r) for r in cursor.fetchall()]
