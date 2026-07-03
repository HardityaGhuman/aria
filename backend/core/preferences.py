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
from psycopg.rows import dict_row

from backend.core import db

# Sensible neutral defaults returned when a user has set nothing.
DEFAULTS = {"tone": "neutral", "response_length": "medium", "language": "English"}


@dataclass
class PreferencesError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: PreferencesError(
        "Could not connect to PostgreSQL for preferences. "
        "Make sure DATABASE_URL points to a running PostgreSQL database."
    ))


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


def _normalize_language(language: str | None) -> str | None:
    """Collapse regional English variants to the canonical default.

    The frontend offers "English (US)"/"English (UK)"; both are plain English to
    the model. Storing them verbatim made ``format_preferences`` emit a redundant
    "Write the answer in English (US)." directive for users who only ever changed
    length. Mapping them back to the default ("English") keeps the prompt clean."""
    if language and language.strip().lower().startswith("english"):
        return DEFAULTS["language"]
    return language


def set_preferences(
    user_id: int,
    tone: str | None = None,
    response_length: str | None = None,
    language: str | None = None,
) -> dict:
    """Upsert the user's preferences; returns the merged-over-defaults result."""
    language = _normalize_language(language)
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


# Map each stored value to an explicit, actionable instruction. A bare
# "length=short" is a weak signal the model tends to ignore; a directive
# ("Keep it brief: …") actually changes the output.
_LENGTH_DIRECTIVES = {
    "short": "Keep the answer brief — a one-sentence lead and at most 2-3 short bullets. Include only the most important points; omit minor detail.",
    "medium": "Use a balanced length — a lead sentence plus a few focused bullets.",
    "long": "Be thorough — cover the relevant specifics, conditions, and exceptions across the bullets.",
}
_TONE_DIRECTIVES = {
    "neutral": "Keep a neutral, professional tone.",
    "warm": "Use a warm, friendly tone.",
    "friendly": "Use a warm, friendly tone.",
    "formal": "Use a formal, businesslike tone.",
    "casual": "Use a relaxed, casual tone.",
}


def format_preferences(prefs: dict) -> str:
    """Render a directive prompt block, or "" when the prefs are all defaults.

    Returning "" for the default case keeps the prompt clean for users who never
    customized anything — no point spending tokens telling the model to be neutral.
    Each non-default value becomes a concrete instruction (not just a key=value)
    so the model actually acts on it."""
    if all(prefs.get(key) == DEFAULTS[key] for key in DEFAULTS):
        return ""

    parts = []
    length = prefs.get("response_length")
    if length in _LENGTH_DIRECTIVES:
        parts.append(_LENGTH_DIRECTIVES[length])
    tone = prefs.get("tone")
    if tone in _TONE_DIRECTIVES:
        parts.append(_TONE_DIRECTIVES[tone])
    language = prefs.get("language")
    if language and language != DEFAULTS["language"]:
        parts.append(f"Write the answer in {language}.")

    if not parts:
        return ""
    return "User preferences (honor these — they override the default answer length/tone):\n- " + "\n- ".join(parts)
