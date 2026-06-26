"""
core/preferences.py
-------------------
Per-user, long-term preferences (tone, response length, language) persisted in
Postgres and injected into the answer prompt. Unlike session memory (which is
conversation-scoped and summarized away), these are durable settings the user
controls once and the assistant honors across every session.

Mirrors core/users.py: ``_connect`` helper, idempotent ``initialize_*``, typed
error, parameterized SQL only.
"""
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core.config import DATABASE_URL

# Sensible neutral defaults returned when a user has set nothing.
DEFAULTS = {"tone": "neutral", "response_length": "medium", "language": "English"}


@dataclass
class PreferencesError(Exception):
    message: str


def _connect():
    try:
        return psycopg.connect(DATABASE_URL)
    except Exception as exc:
        raise PreferencesError(
            "Could not connect to PostgreSQL for preferences. "
            "Make sure DATABASE_URL points to a running PostgreSQL database."
        ) from exc


def initialize_preferences_table() -> None:
    """Create the ``user_preferences`` table if absent. Idempotent."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id BIGINT PRIMARY KEY,
                    tone TEXT,
                    response_length TEXT,
                    language TEXT,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )


def get_preferences(user_id: int) -> dict:
    """Return the user's preferences merged over defaults (always complete)."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT tone, response_length, language FROM user_preferences WHERE user_id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
    merged = dict(DEFAULTS)
    if row:
        for key in DEFAULTS:
            if row.get(key):
                merged[key] = row[key]
    return merged


def set_preferences(
    user_id: int,
    tone: str | None = None,
    response_length: str | None = None,
    language: str | None = None,
) -> dict:
    """Upsert the user's preferences; returns the merged-over-defaults result."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_preferences (user_id, tone, response_length, language, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET
                    tone = EXCLUDED.tone,
                    response_length = EXCLUDED.response_length,
                    language = EXCLUDED.language,
                    updated_at = now()
                """,
                (user_id, tone, response_length, language),
            )
    return get_preferences(user_id)


def format_preferences(prefs: dict) -> str:
    """Render a one-line prompt block, or "" when the prefs are all defaults.

    Returning "" for the default case keeps the prompt clean for users who never
    customized anything — no point spending tokens telling the model to be neutral."""
    if all(prefs.get(key) == DEFAULTS[key] for key in DEFAULTS):
        return ""
    return (
        "User preferences (honor these in your answer): "
        f"tone={prefs.get('tone')}, length={prefs.get('response_length')}, "
        f"language={prefs.get('language')}."
    )
