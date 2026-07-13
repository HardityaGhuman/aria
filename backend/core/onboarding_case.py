"""core/onboarding_case.py
-----------------------
Our workflow-state projection of an onboarding request — NOT the grant itself (that
lives in the IdP). Two tables: `onboarding_cases` (queryable status) and
`onboarding_case_audit` (append-only transition log). Mirrors jira_case.py; distinct
from the LangGraph checkpointer (the graph's resume state).

The state machine is enforced here: `transition` rejects any change not in
LEGAL_TRANSITIONS and writes the audit row in the SAME transaction as the status
update, so status and audit can never diverge.

TWO failure statuses, and the difference is the whole point of this slice:
  write_failed — PERMANENT. The connector refused the request; it will refuse it
                 again. Terminal. A human files a new Case.
  dead_letter  — TRANSIENT, survived the retry budget (or the breaker was open).
                 The ONLY non-terminal failure state: an admin can replay it from
                 its checkpoint, and idempotency-by-case_id makes that safe.
The DLQ is a QUERY (`WHERE status = 'dead_letter'`), not a second table. YAGNI.

No `risk_tier` column: with a single manager gate the field would be dead, and a
column nothing reads is a lie about the system."""
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
    "approved": {"provisioned", "write_failed", "dead_letter"},
    "dead_letter": {"approved"},          # replay re-enters the write
    "denied_policy": set(),
    "denied_manager": set(),
    "unroutable": set(),
    "provisioned": set(),
    "write_failed": set(),
}


@dataclass
class OnboardingCaseError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: OnboardingCaseError("Could not connect to PostgreSQL for onboarding cases."))


def initialize_onboarding_case_tables() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS onboarding_cases (
                    case_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    employee_email  TEXT NOT NULL,
                    approver_email  TEXT,
                    role            TEXT,
                    tools           TEXT[] NOT NULL DEFAULT '{}',
                    status          TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','pending_approval','denied_policy',
                                          'unroutable','approved','denied_manager',
                                          'provisioned','write_failed','dead_letter')),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    grant_id        TEXT,
                    attempt         INTEGER NOT NULL DEFAULT 0,
                    failure_reason  TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS onboarding_case_audit (
                    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    case_id    UUID NOT NULL REFERENCES onboarding_cases(case_id),
                    event      TEXT NOT NULL,
                    actor_id   TEXT,
                    detail     TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_onboarding_case_audit_case "
                "ON onboarding_case_audit (case_id, id)"
            )
            # The DLQ is a query, so give it an index.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_onboarding_cases_status "
                "ON onboarding_cases (status)"
            )


def create_case(employee_email, approver_email, role, tools, idempotency_key) -> dict:
    """Insert a draft Case. On idempotency_key collision, return the existing row
    (no second draft, no second audit row)."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO onboarding_cases
                        (employee_email, approver_email, role, tools, idempotency_key)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (employee_email, approver_email, role, list(tools), idempotency_key),
                )
                row = dict(cursor.fetchone())
                cursor.execute(
                    "INSERT INTO onboarding_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                    (row["case_id"], "drafted", employee_email, f"{role}: {len(list(tools))} tools"),
                )
                return row
            except psycopg.errors.UniqueViolation:
                connection.rollback()
                cursor.execute("SELECT * FROM onboarding_cases WHERE idempotency_key = %s", (idempotency_key,))
                return dict(cursor.fetchone())


def get_case(case_id: str) -> dict | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT * FROM onboarding_cases WHERE case_id = %s", (case_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


def transition(case_id, new_status, actor_id, detail, *, grant_id=None,
               attempt=None, failure_reason=None) -> dict:
    """Move a Case to new_status iff the transition is legal, appending an audit row
    in the same transaction. Raises OnboardingCaseError on an illegal or unknown-case
    move. `attempt` / `failure_reason` are the execution-memory fields the DLQ and a
    replaying admin need; COALESCE keeps them once set."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT status FROM onboarding_cases WHERE case_id = %s FOR UPDATE", (case_id,))
            current = cursor.fetchone()
            if current is None:
                raise OnboardingCaseError(f"no such case {case_id}")
            cur_status = current["status"]
            if new_status not in LEGAL_TRANSITIONS.get(cur_status, set()):
                raise OnboardingCaseError(f"illegal transition {cur_status} -> {new_status}")
            cursor.execute(
                """
                UPDATE onboarding_cases
                SET status = %s,
                    grant_id = COALESCE(%s, grant_id),
                    attempt = COALESCE(%s, attempt),
                    failure_reason = COALESCE(%s, failure_reason),
                    updated_at = now()
                WHERE case_id = %s
                RETURNING *
                """,
                (new_status, grant_id, attempt, failure_reason, case_id),
            )
            row = dict(cursor.fetchone())
            cursor.execute(
                "INSERT INTO onboarding_case_audit (case_id, event, actor_id, detail) VALUES (%s,%s,%s,%s)",
                (case_id, new_status, actor_id, detail),
            )
            return row


def list_audit(case_id: str) -> list[dict]:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT event, actor_id, detail, created_at FROM onboarding_case_audit "
                "WHERE case_id = %s ORDER BY id",
                (case_id,),
            )
            return [dict(r) for r in cursor.fetchall()]


def list_dead_letter() -> list[dict]:
    """The DLQ: replayable Cases. A query, not a table."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM onboarding_cases WHERE status = 'dead_letter' ORDER BY updated_at DESC"
            )
            return [dict(r) for r in cursor.fetchall()]
