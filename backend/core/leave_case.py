"""core/leave_case.py
-------------------
Our workflow-state projection of a leave request — NOT the booking, NOT the balance
(those live in the HRIS). Two tables: ``leave_cases`` (queryable status) and
``leave_case_audit`` (append-only, immutable transition log). Distinct from the
LangGraph checkpointer (the graph's resume state).

The lifecycle and the transition engine now live in ``core/write/case_store.py``; this
module owns only the table shape, the agent's spec, and the typed business columns. That
is the retrofit: Leave used to dead-end at ``write_failed`` on the first connector error
because its hand-written transition table had nowhere else to go — no ``dead_letter``, so
no DLQ, so a network blip destroyed an approved request permanently.
"""
from backend.core import db
from backend.core.write import case_store
from backend.core.write.case_store import CaseSpec, WriteCaseError

LEAVE_SPEC = CaseSpec(
    agent="leave",
    table="leave_cases",
    audit_table="leave_case_audit",
    success_status="booked",
    result_column="confirmation_id",
    summary_columns=("start_date", "end_date", "days", "reason"),
)

# Back-compat aliases: callers and tests still import these names.
LeaveCaseError = WriteCaseError
LEGAL_TRANSITIONS = LEAVE_SPEC.legal_transitions()


def _connect():
    return db.pooled(lambda: WriteCaseError("Could not connect to PostgreSQL for leave cases."))


def initialize_leave_case_tables() -> None:
    """Idempotent startup DDL — re-runnable on every boot. The ALTERs are the migration:
    Leave predates the write-boundary reliability layer, so it lacks the control columns
    (attempt / failure_reason) and the dead_letter status the retry edge needs."""
    statuses = ", ".join(f"'{s}'" for s in LEAVE_SPEC.statuses())
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
                    status          TEXT NOT NULL DEFAULT 'draft',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    confirmation_id TEXT,
                    attempt         INTEGER NOT NULL DEFAULT 0,
                    failure_reason  TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "ALTER TABLE leave_cases ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 0"
            )
            cursor.execute("ALTER TABLE leave_cases ADD COLUMN IF NOT EXISTS failure_reason TEXT")
            cursor.execute(
                "ALTER TABLE leave_cases DROP CONSTRAINT IF EXISTS leave_cases_status_check"
            )
            cursor.execute(
                f"ALTER TABLE leave_cases ADD CONSTRAINT leave_cases_status_check "
                f"CHECK (status IN ({statuses}))"
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
            # The DLQ is a query, so give it an index.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_leave_cases_status ON leave_cases (status)"
            )


def create_case(employee_email, approver_email, start_date, end_date, days, reason,
                idempotency_key) -> dict:
    return case_store.create_case(
        LEAVE_SPEC, employee_email, approver_email, idempotency_key,
        start_date=start_date, end_date=end_date, days=days, reason=reason,
    )


def get_case(case_id: str) -> dict | None:
    return case_store.get_case(LEAVE_SPEC, case_id)


def get_case_by_idempotency_key(idempotency_key: str) -> dict | None:
    return case_store.get_by_idempotency_key(LEAVE_SPEC, idempotency_key)


def transition(case_id, new_status, actor_id, detail, *, confirmation_id=None,
               attempt=None, failure_reason=None) -> dict:
    return case_store.transition(
        LEAVE_SPEC, case_id, new_status, actor_id, detail,
        confirmation_id=confirmation_id, attempt=attempt, failure_reason=failure_reason,
    )


def list_audit(case_id: str) -> list[dict]:
    return case_store.list_audit(LEAVE_SPEC, case_id)
