"""core/leave_case.py
-------------------
Our workflow-state projection of a leave request — NOT the booking, NOT the balance
(those live in the HRIS). Two tables: ``leave_cases`` (queryable status) and
``leave_case_audit`` (append-only, immutable transition log). Distinct from the
LangGraph checkpointer (the graph's resume state); same split as
ingestion_jobs vs document_status.

The state machine is enforced here: ``transition`` rejects any status change not in
LEGAL_TRANSITIONS, and writes the audit row in the SAME transaction as the status
update, so status and audit can never diverge."""
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core import db

# Exhaustive, one-way lifecycle. Terminal statuses map to an empty set.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_approval", "denied_policy", "unroutable"},
    "pending_approval": {"approved", "denied_manager"},
    "approved": {"booked", "write_failed"},
    "denied_policy": set(),
    "denied_manager": set(),
    "booked": set(),
    "write_failed": set(),
    "unroutable": set(),
}


@dataclass
class LeaveCaseError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: LeaveCaseError("Could not connect to PostgreSQL for leave cases."))


def initialize_leave_case_tables() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS leave_cases (
                    case_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    employee_email  TEXT NOT NULL,
                    approver_email  TEXT,
                    start_date      DATE NOT NULL,
                    end_date        DATE NOT NULL,
                    days            INT  NOT NULL,
                    reason          TEXT,
                    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','pending_approval','denied_policy',
                                          'approved','denied_manager','booked',
                                          'write_failed','unroutable')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    confirmation_id TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS leave_case_audit (
                    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    case_id    UUID NOT NULL REFERENCES leave_cases(case_id),
                    event      TEXT NOT NULL,
                    actor_id   TEXT,
                    detail     TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_leave_case_audit_case ON leave_case_audit (case_id, id)"
            )


def create_case(employee_email, approver_email, start_date, end_date, days, reason, idempotency_key) -> dict:
    """Insert a draft Case. On idempotency_key collision, return the existing row
    (no second draft, no second audit row)."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO leave_cases
                        (employee_email, approver_email, start_date, end_date, days, reason, idempotency_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (employee_email, approver_email, start_date, end_date, days, reason, idempotency_key),
                )
                row = dict(cursor.fetchone())
                cursor.execute(
                    "INSERT INTO leave_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                    (row["case_id"], "drafted", employee_email, f"{days}d {start_date}..{end_date}"),
                )
                return row
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                cursor.execute("SELECT * FROM leave_cases WHERE idempotency_key = %s", (idempotency_key,))
                return dict(cursor.fetchone())


def get_case(case_id: str) -> dict | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM leave_cases WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


def transition(case_id, new_status, actor_id, detail, *, confirmation_id=None) -> dict:
    """Move a Case to new_status iff the transition is legal, appending an audit row
    in the same transaction. Raises LeaveCaseError on an illegal or unknown-case move."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT status FROM leave_cases WHERE case_id = %s FOR UPDATE", (case_id,))
            current = cursor.fetchone()
            if current is None:
                raise LeaveCaseError(f"no such case {case_id}")
            cur_status = current["status"]
            if new_status not in LEGAL_TRANSITIONS.get(cur_status, set()):
                raise LeaveCaseError(f"illegal transition {cur_status} -> {new_status}")
            cursor.execute(
                """
                UPDATE leave_cases
                SET status = %s,
                    confirmation_id = COALESCE(%s, confirmation_id),
                    updated_at = now()
                WHERE case_id = %s
                RETURNING *
                """,
                (new_status, confirmation_id, case_id),
            )
            row = dict(cursor.fetchone())
            cursor.execute(
                "INSERT INTO leave_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                (case_id, new_status, actor_id, detail),
            )
            return row


def list_audit(case_id: str) -> list[dict]:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT event, actor_id, detail, created_at FROM leave_case_audit WHERE case_id = %s ORDER BY id",
                (case_id,),
            )
            return [dict(r) for r in cursor.fetchall()]
