"""core/jira_case.py
------------------
Our workflow-state projection of a work request — NOT the Jira issue itself (that lives
in the tracker). Two tables: `jira_cases` (queryable status) and `jira_case_audit`
(append-only transition log). Mirrors leave_case.py; distinct from the LangGraph
checkpointer (the graph's resume state).

The state machine is enforced here: `transition` rejects any change not in
LEGAL_TRANSITIONS and writes the audit row in the SAME transaction as the status update,
so status and audit can never diverge."""
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core import db

# Exhaustive, one-way lifecycle. Terminal statuses map to an empty set.
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_approval", "denied_validation", "unroutable"},
    "pending_approval": {"approved", "denied_approver"},
    "approved": {"created", "write_failed"},
    "denied_validation": set(),
    "unroutable": set(),
    "denied_approver": set(),
    "created": set(),
    "write_failed": set(),
}


@dataclass
class JiraCaseError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: JiraCaseError("Could not connect to PostgreSQL for jira cases."))


def initialize_jira_case_tables() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_cases (
                    case_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    employee_email  TEXT NOT NULL,
                    approver_email  TEXT,
                    project         TEXT,
                    issue_type      TEXT,
                    summary         TEXT NOT NULL,
                    description     TEXT,
                    risk_tier       TEXT NOT NULL DEFAULT 'standard',
                    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','pending_approval','denied_validation',
                                          'unroutable','approved','denied_approver',
                                          'created','write_failed')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    issue_key       TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS jira_case_audit (
                    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    case_id    UUID NOT NULL REFERENCES jira_cases(case_id),
                    event      TEXT NOT NULL,
                    actor_id   TEXT,
                    detail     TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_jira_case_audit_case ON jira_case_audit (case_id, id)"
            )


def create_case(employee_email, approver_email, project, issue_type, summary, description, idempotency_key) -> dict:
    """Insert a draft Case. On idempotency_key collision, return the existing row
    (no second draft, no second audit row)."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO jira_cases
                        (employee_email, approver_email, project, issue_type, summary, description, idempotency_key)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (employee_email, approver_email, project, issue_type, summary, description, idempotency_key),
                )
                row = dict(cursor.fetchone())
                cursor.execute(
                    "INSERT INTO jira_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                    (row["case_id"], "drafted", employee_email, f"{project}/{issue_type}: {summary}"),
                )
                return row
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                cursor.execute("SELECT * FROM jira_cases WHERE idempotency_key = %s", (idempotency_key,))
                return dict(cursor.fetchone())


def get_case(case_id: str) -> dict | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM jira_cases WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


def transition(case_id, new_status, actor_id, detail, *, issue_key=None) -> dict:
    """Move a Case to new_status iff the transition is legal, appending an audit row
    in the same transaction. Raises JiraCaseError on an illegal or unknown-case move."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT status FROM jira_cases WHERE case_id = %s FOR UPDATE", (case_id,))
            current = cursor.fetchone()
            if current is None:
                raise JiraCaseError(f"no such case {case_id}")
            cur_status = current["status"]
            if new_status not in LEGAL_TRANSITIONS.get(cur_status, set()):
                raise JiraCaseError(f"illegal transition {cur_status} -> {new_status}")
            cursor.execute(
                """
                UPDATE jira_cases
                SET status = %s,
                    issue_key = COALESCE(%s, issue_key),
                    updated_at = now()
                WHERE case_id = %s
                RETURNING *
                """,
                (new_status, issue_key, case_id),
            )
            row = dict(cursor.fetchone())
            cursor.execute(
                "INSERT INTO jira_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                (case_id, new_status, actor_id, detail),
            )
            return row


def list_audit(case_id: str) -> list[dict]:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT event, actor_id, detail, created_at FROM jira_case_audit WHERE case_id = %s ORDER BY id",
                (case_id,),
            )
            return [dict(r) for r in cursor.fetchall()]
