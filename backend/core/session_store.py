"""
core/session_store.py
----------------------
The seam between "where conversation sessions live" and the rest of the app.
Today sessions are rows in Postgres (``chat_sessions``); the serverless step may
move hot session metadata to Redis/Memorystore. Routes depend on the
``SessionStore`` *interface*, not on Postgres, so that swap is a new class — not
a route rewrite.

A session is user-owned (``owner_user_id``). That ownership is what makes
per-user listing and the 403-on-someone-else's-session check possible.
"""
import uuid
from typing import Protocol

# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core import db
from backend.core.chat_memory import ChatMemoryError


class SessionStore(Protocol):
    """Storage-agnostic contract for user-owned conversation sessions."""

    def create(self, owner_user_id: int, title: str | None = None) -> str: ...
    def list_for_owner(self, owner_user_id: int) -> list[dict]: ...
    def owner_of(self, session_id: str) -> int | None: ...
    def rename(self, session_id: str, title: str) -> None: ...
    def delete(self, session_id: str) -> None: ...


class PostgresSessionStore:
    """``SessionStore`` backed by the ``chat_sessions`` table (parameterized SQL)."""

    def _connect(self):
        return db.pooled(lambda: ChatMemoryError(
            "Could not connect to PostgreSQL for sessions. "
            "Make sure DATABASE_URL points to a running PostgreSQL database."
        ))

    def create(self, owner_user_id: int, title: str | None = None) -> str:
        session_id = uuid.uuid4().hex
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO chat_sessions (session_id, owner_user_id, title)
                    VALUES (%s, %s, %s)
                    """,
                    (session_id, owner_user_id, title),
                )
        return session_id

    def list_for_owner(self, owner_user_id: int) -> list[dict]:
        with self._connect() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT session_id, title, updated_at
                    FROM chat_sessions
                    WHERE owner_user_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (owner_user_id,),
                )
                return [dict(row) for row in cursor.fetchall()]

    def owner_of(self, session_id: str) -> int | None:
        """Return the owner's user id, or None if the session does not exist."""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT owner_user_id FROM chat_sessions WHERE session_id = %s",
                    (session_id,),
                )
                row = cursor.fetchone()
                return row[0] if row else None

    def rename(self, session_id: str, title: str) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE chat_sessions SET title = %s, updated_at = now() WHERE session_id = %s",
                    (title, session_id),
                )

    def delete(self, session_id: str) -> None:
        # Cascades to chat_messages via the FK ON DELETE CASCADE.
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))


# Module-level default the routes import. Swapping to Redis later = reassign this
# to a RedisSessionStore implementing the same Protocol; routes don't change.
session_store: SessionStore = PostgresSessionStore()
