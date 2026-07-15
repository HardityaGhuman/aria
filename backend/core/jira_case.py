"""core/jira_case.py
------------------
Our workflow-state projection of a work request — NOT the Jira issue itself (that lives
in the tracker). Two tables: `jira_cases` (queryable status) and `jira_case_audit`
(append-only transition log). Distinct from the LangGraph checkpointer (the graph's
resume state).

The lifecycle and the transition engine now live in `core/write/case_store.py`; this
module owns only the table shape, the agent's spec, and the typed business columns.

Two schema debts are paid here. `risk_tier` is dropped — it was never read and never
written, and a column nothing reads is a lie about what the system enforces. And Jira's
private status dialect (`denied_validation` / `denied_approver`) is renamed to the shared
`denied_policy` / `denied_manager`, because one vocabulary is what lets a single DLQ and
a single approvals inbox group Cases across all three agents.
"""
from backend.core import db
from backend.core.write import case_store
from backend.core.write.case_store import CaseSpec, WriteCaseError

JIRA_SPEC = CaseSpec(
    agent="jira",
    table="jira_cases",
    audit_table="jira_case_audit",
    success_status="created",
    result_column="issue_key",
    summary_columns=("project", "issue_type", "summary"),
)

# Back-compat aliases: callers and tests still import these names.
JiraCaseError = WriteCaseError
LEGAL_TRANSITIONS = JIRA_SPEC.legal_transitions()


def _connect():
    return db.pooled(lambda: WriteCaseError("Could not connect to PostgreSQL for jira cases."))


def initialize_jira_case_tables() -> None:
    """Idempotent startup DDL — re-runnable on every boot. The ALTERs + UPDATEs are the
    migration. Order matters: the old CHECK constraint does not know the new status words,
    so it must be DROPPED before the UPDATEs rewrite them, and re-added afterwards."""
    statuses = ", ".join(f"'{s}'" for s in JIRA_SPEC.statuses())
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
                    status          TEXT NOT NULL DEFAULT 'draft',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    issue_key       TEXT,
                    attempt         INTEGER NOT NULL DEFAULT 0,
                    failure_reason  TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                "ALTER TABLE jira_cases ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 0"
            )
            cursor.execute("ALTER TABLE jira_cases ADD COLUMN IF NOT EXISTS failure_reason TEXT")
            cursor.execute("ALTER TABLE jira_cases DROP COLUMN IF EXISTS risk_tier")
            cursor.execute(
                "ALTER TABLE jira_cases DROP CONSTRAINT IF EXISTS jira_cases_status_check"
            )
            cursor.execute(
                "UPDATE jira_cases SET status = 'denied_policy' WHERE status = 'denied_validation'"
            )
            cursor.execute(
                "UPDATE jira_cases SET status = 'denied_manager' WHERE status = 'denied_approver'"
            )
            cursor.execute(
                f"ALTER TABLE jira_cases ADD CONSTRAINT jira_cases_status_check "
                f"CHECK (status IN ({statuses}))"
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
            # The DLQ is a query, so give it an index.
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_jira_cases_status ON jira_cases (status)"
            )


def create_case(employee_email, approver_email, project, issue_type, summary, description,
                idempotency_key) -> dict:
    return case_store.create_case(
        JIRA_SPEC, employee_email, approver_email, idempotency_key,
        project=project, issue_type=issue_type, summary=summary, description=description,
    )


def get_case(case_id: str) -> dict | None:
    return case_store.get_case(JIRA_SPEC, case_id)


def get_case_by_idempotency_key(idempotency_key: str) -> dict | None:
    return case_store.get_by_idempotency_key(JIRA_SPEC, idempotency_key)


def transition(case_id, new_status, actor_id, detail, *, issue_key=None,
               attempt=None, failure_reason=None) -> dict:
    return case_store.transition(
        JIRA_SPEC, case_id, new_status, actor_id, detail,
        issue_key=issue_key, attempt=attempt, failure_reason=failure_reason,
    )


def list_audit(case_id: str) -> list[dict]:
    return case_store.list_audit(JIRA_SPEC, case_id)
