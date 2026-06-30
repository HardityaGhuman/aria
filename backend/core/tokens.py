"""
core/tokens.py
--------------
Persistence for refresh-token state: the server tracks each issued refresh
token's ``jti`` so it can rotate (revoke the old jti on every refresh) and
revoke on logout. Stateless access tokens are NOT stored — only refresh tokens,
which are long-lived and must be revocable.

Mirrors core/users.py: a ``_connect`` helper, an idempotent ``initialize_*``,
a typed error, parameterized SQL only. Postgres now; a Redis store can implement
the same four functions later without changing callers.
"""
from dataclasses import dataclass
from datetime import datetime

from backend.core import db


@dataclass
class TokenError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: TokenError(
        "Could not connect to PostgreSQL for refresh tokens. "
        "Make sure DATABASE_URL points to a running PostgreSQL database."
    ))


def initialize_tokens_table() -> None:
    """Create the ``refresh_tokens`` table if absent. Idempotent."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    jti TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    revoked BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )


def store_refresh(jti: str, user_id: int, expires_at: datetime) -> None:
    """Record a freshly issued refresh token."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO refresh_tokens (jti, user_id, expires_at) VALUES (%s, %s, %s)",
                (jti, user_id, expires_at),
            )


def is_valid(jti: str) -> bool:
    """True if the jti exists, is not revoked, and has not expired."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM refresh_tokens "
                "WHERE jti = %s AND revoked = false AND expires_at > now()",
                (jti,),
            )
            return cursor.fetchone() is not None


def revoke(jti: str) -> None:
    """Revoke a single refresh token (used on rotation and logout)."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE refresh_tokens SET revoked = true WHERE jti = %s", (jti,))


def revoke_all(user_id: int) -> None:
    """Revoke every refresh token for a user (e.g. password change, force logout)."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE refresh_tokens SET revoked = true WHERE user_id = %s", (user_id,)
            )
