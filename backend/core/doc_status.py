"""
core/doc_status.py
------------------
Per-document ingestion status for the admin document lifecycle. A document moves
``queued -> processing -> indexed`` (or ``failed``) as it is ingested. The status
is keyed by ``source`` — the path relative to the docs root (e.g.
``hr/employment-basics.md``), the same id used as the Chroma ``source`` metadata.

Mirrors core/users.py: ``_connect`` helper, idempotent ``initialize_*``, typed
error, parameterized SQL only.
"""
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core.config import DATABASE_URL

VALID_STATUSES = ("queued", "processing", "indexed", "failed")


@dataclass
class DocStatusError(Exception):
    message: str


def _connect():
    try:
        return psycopg.connect(DATABASE_URL)
    except Exception as exc:
        raise DocStatusError(
            "Could not connect to PostgreSQL for document status. "
            "Make sure DATABASE_URL points to a running PostgreSQL database."
        ) from exc


def initialize_doc_status_table() -> None:
    """Create the ``document_status`` table if absent. Idempotent."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS document_status (
                    source TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (status IN ('queued','processing','indexed','failed')),
                    error TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )


def set_status(source: str, status: str, error: str | None = None) -> None:
    """Upsert a document's status."""
    if status not in VALID_STATUSES:
        raise DocStatusError(f"Invalid status {status!r}")
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_status (source, status, error, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (source) DO UPDATE
                  SET status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()
                """,
                (source, status, error),
            )


def get_status(source: str) -> dict | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT source, status, error, updated_at FROM document_status WHERE source = %s",
                (source,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None


def all_statuses() -> dict[str, dict]:
    """Return ``{source: {status, error, updated_at}}`` for every tracked doc."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT source, status, error, updated_at FROM document_status")
            return {r["source"]: dict(r) for r in cursor.fetchall()}


def delete_status(source: str) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM document_status WHERE source = %s", (source,))
