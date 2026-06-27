from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row
# pyrefly: ignore [missing-import]
from psycopg.types.json import Jsonb

from backend.core.config import DATABASE_URL


@dataclass
class ChatMemoryError(Exception):
    message: str

# You can't run SQL on a connection directly in psycopg — 
# you must go through a cursor. So any function that talks to the DB needs one. 
# That's why it appears in all 9 functions, not because it's special, 
# but because it's the mandatory entry point for executing a query.

def _connect():
    try:
        return psycopg.connect(DATABASE_URL)
    except Exception as exc:
        raise ChatMemoryError(
            "Could not connect to PostgreSQL chat memory. "
            "Make sure DATABASE_URL points to a running PostgreSQL database."
        ) from exc


def _ensure_session(cursor, session_id: str, owner_user_id: int | None = None) -> None:
    # On first insert, stamp the owner so the session is user-scoped. On conflict
    # we only bump updated_at and never overwrite an existing owner (COALESCE keeps
    # the original), so a chat call can't silently reassign someone else's session.
    cursor.execute(
        """
        INSERT INTO chat_sessions (session_id, owner_user_id)
        VALUES (%s, %s)
        ON CONFLICT (session_id)
        DO UPDATE SET
            updated_at = now(),
            owner_user_id = COALESCE(chat_sessions.owner_user_id, EXCLUDED.owner_user_id)
        """,
        (session_id, owner_user_id),
    )


def initialize_chat_memory() -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary TEXT
                """
            )
            # Identity is the connective tissue: a session is owned by a user, which
            # is what later makes per-user listing, preferences, and RBAC coherent.
            cursor.execute(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS owner_user_id BIGINT"
            )
            cursor.execute(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title TEXT"
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner
                ON chat_sessions(owner_user_id, updated_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session_id_id
                ON chat_messages(session_id, id)
                """
            )
            # Per-message source citations (assistant rows only). Stored as the
            # frozen Source shape so reopening a chat restores the same citations
            # the live answer showed. Added by migration for existing installs.
            cursor.execute(
                "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sources JSONB"
            )


def append_exchange(
    session_id: str,
    user_message: str,
    assistant_reply: str,
    owner_user_id: int | None = None,
    sources: list[dict] | None = None,
) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_session(cursor, session_id, owner_user_id)
            # Citations belong to the assistant turn only; the user row stores NULL.
            # Jsonb adapts the Python list to the jsonb column (parameterized, never
            # string-interpolated — the SQL-injection invariant holds for JSON too).
            assistant_sources = Jsonb(sources) if sources else None
            cursor.executemany(
                """
                INSERT INTO chat_messages (session_id, role, content, sources)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (session_id, "user", user_message, None),
                    (session_id, "assistant", assistant_reply, assistant_sources),
                ],
            )
            cursor.execute(
                "UPDATE chat_sessions SET updated_at = now() WHERE session_id = %s",
                (session_id,),
            )


def _get_history(session_id: str, limit: int | None = None, include_ids: bool = False) -> list[dict]:
    params: list[object] = [session_id]
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT %s"
        params.append(limit)

    selected_columns = "id, role, content, sources" if include_ids else "role, content, sources"

    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT {selected_columns}
                FROM (
                    SELECT id, role, content, sources
                    FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY id DESC
                    {limit_clause}
                ) recent_messages
                ORDER BY id ASC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def get_history(session_id: str, limit: int | None = None) -> list[dict]:
    return _get_history(session_id, limit)


def clear_history(session_id: str) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))


def get_session_summary(session_id: str) -> str | None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT summary FROM chat_sessions WHERE session_id = %s",
                (session_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None


def update_session_summary(session_id: str, summary: str | None) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_session(cursor, session_id)
            cursor.execute(
                "UPDATE chat_sessions SET summary = %s, updated_at = now() WHERE session_id = %s",
                (summary, session_id),
            )


def delete_messages_before_id(session_id: str, message_id: int) -> None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM chat_messages WHERE session_id = %s AND id <= %s",
                (session_id, message_id),
            )


def get_history_with_ids(session_id: str, limit: int | None = None) -> list[dict]:
    return _get_history(session_id, limit, include_ids=True)
