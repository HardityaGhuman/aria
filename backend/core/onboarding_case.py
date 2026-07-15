"""core/onboarding_case.py
-----------------------
Our workflow-state projection of an onboarding request — NOT the grant itself (that
lives in the IdP). Two tables: `onboarding_cases` (queryable status) and
`onboarding_case_audit` (append-only transition log). Distinct from the LangGraph
checkpointer (the graph's resume state).

The lifecycle and the transition engine now live in `core/write/case_store.py`; this
module owns only the table shape, the agent's spec, and the typed business columns.
Onboarding was the agent that HAD the reliability layer — the retrofit is about the other
two catching up, so nothing here changes semantically.

TWO failure statuses, and the difference is the whole point:
  write_failed — PERMANENT. The connector refused the request; it will refuse it
                 again. Terminal. A human files a new Case.
  dead_letter  — TRANSIENT, survived the retry budget (or the breaker was open).
                 The ONLY non-terminal failure state: an admin can replay it from
                 its checkpoint, and idempotency-by-case_id makes that safe.
The DLQ is a QUERY (`WHERE status = 'dead_letter'`), not a second table. YAGNI.

No `risk_tier` column: with a single manager gate the field would be dead, and a
column nothing reads is a lie about the system."""
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core import db
from backend.core.write import case_store
from backend.core.write.case_store import CaseSpec, WriteCaseError

ONBOARDING_SPEC = CaseSpec(
    agent="onboarding",
    table="onboarding_cases",
    audit_table="onboarding_case_audit",
    success_status="provisioned",
    result_column="grant_id",
    summary_columns=("role", "tools"),
)

# Back-compat aliases: callers and tests still import these names.
OnboardingCaseError = WriteCaseError
LEGAL_TRANSITIONS = ONBOARDING_SPEC.legal_transitions()


def _connect():
    return db.pooled(
        lambda: WriteCaseError("Could not connect to PostgreSQL for onboarding cases.")
    )


def initialize_onboarding_case_tables() -> None:
    statuses = ", ".join(f"'{s}'" for s in ONBOARDING_SPEC.statuses())
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
                    status          TEXT NOT NULL DEFAULT 'draft',
                    idempotency_key TEXT NOT NULL UNIQUE,
                    grant_id        TEXT,
                    attempt         INTEGER NOT NULL DEFAULT 0,
                    failure_reason  TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # The CHECK now comes from the spec, like the other two agents — one source of
            # truth for the status vocabulary.
            cursor.execute(
                "ALTER TABLE onboarding_cases DROP CONSTRAINT IF EXISTS onboarding_cases_status_check"
            )
            cursor.execute(
                f"ALTER TABLE onboarding_cases ADD CONSTRAINT onboarding_cases_status_check "
                f"CHECK (status IN ({statuses}))"
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
    return case_store.create_case(
        ONBOARDING_SPEC, employee_email, approver_email, idempotency_key,
        role=role, tools=list(tools),
    )


def get_case_by_idempotency_key(idempotency_key: str) -> dict | None:
    """Look a Case up by intent key. The route calls this BEFORE it extracts, so a
    duplicate submit is answered from the row — no second LLM call, no second graph
    invocation on a thread that is already parked at the approval gate."""
    return case_store.get_by_idempotency_key(ONBOARDING_SPEC, idempotency_key)


def get_case(case_id: str) -> dict | None:
    return case_store.get_case(ONBOARDING_SPEC, case_id)


def transition(case_id, new_status, actor_id, detail, *, grant_id=None,
               attempt=None, failure_reason=None) -> dict:
    return case_store.transition(
        ONBOARDING_SPEC, case_id, new_status, actor_id, detail,
        grant_id=grant_id, attempt=attempt, failure_reason=failure_reason,
    )


def list_audit(case_id: str) -> list[dict]:
    return case_store.list_audit(ONBOARDING_SPEC, case_id)


def list_dead_letter() -> list[dict]:
    """The DLQ: replayable Cases. A query, not a table. (Task 9 moves this to the shared
    engine so one admin surface can drain every agent's queue.)"""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT * FROM onboarding_cases WHERE status = 'dead_letter' "
                "ORDER BY updated_at DESC"
            )
            return [dict(r) for r in cursor.fetchall()]
