"""
core/users.py
-------------
PostgreSQL access for the ``users`` table: the identity store behind auth and
RBAC. Mirrors ``core/chat_memory.py`` (a ``_connect`` helper, an idempotent
``initialize_*`` function, a typed error, parameterized SQL only).

Note on the memory split: this table holds long-term identity and lives in
Postgres for good. Short-term chat sessions/messages are destined for a Redis
cache later, so they stay separate from this module.
"""
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg.rows import dict_row

from backend.core import db


@dataclass
class UserError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: UserError(
        "Could not connect to PostgreSQL for users. "
        "Make sure DATABASE_URL points to a running PostgreSQL database."
    ))


def initialize_users_table() -> None:
    """Create the ``users`` table if it does not exist. Idempotent.

    Also upgrades the role CHECK constraint on pre-existing DBs so the
    ``manager`` role is accepted. ``CREATE TABLE IF NOT EXISTS`` silently
    skips the CREATE when the table already exists, so the ALTER TABLE lines
    below handle in-place migration without data loss.
    """
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('hr', 'manager', 'employee')),
                    region TEXT NOT NULL DEFAULT 'us' CHECK (region IN ('us', 'india')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # Idempotent upgrade for DBs created before the manager role existed.
            cursor.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
            cursor.execute(
                "ALTER TABLE users ADD CONSTRAINT users_role_check "
                "CHECK (role IN ('hr', 'manager', 'employee'))"
            )
            # Idempotent migration: add region column if this is a pre-existing DB.
            cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS region TEXT NOT NULL DEFAULT 'us'")
            cursor.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_region_check")
            cursor.execute("ALTER TABLE users ADD CONSTRAINT users_region_check CHECK (region IN ('us', 'india'))")


def create_user(email: str, password_hash: str, role: str, region: str = "us") -> dict:
    """Insert a user. Returns the created row. Raises on duplicate email."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO users (email, password_hash, role, region)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, email, role, region, created_at
                    """,
                    (email, password_hash, role, region),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise UserError(f"A user with email {email!r} already exists.") from exc
            return dict(cursor.fetchone())


def get_user_by_email(email: str) -> dict | None:
    """Return the full user row (incl. password_hash) or None."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id, email, password_hash, role, region, created_at FROM users WHERE email = %s",
                (email,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    """Return the user row (no password_hash) or None."""
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT id, email, role, region, created_at FROM users WHERE id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
