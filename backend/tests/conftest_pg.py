"""tests/conftest_pg.py
--------------------
Shared skip helper for pg-integration tests. The default suite must pass with
no live pgvector, so any test hitting the real store is marked `@requires_pg`
and skips when the `vector` extension is absent locally.
"""
import pytest


def pg_available() -> bool:
    """True when the local Postgres has the pgvector extension available."""
    try:
        from backend.core.db import connection
        with connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM pg_available_extensions WHERE name='vector'"
            ).fetchone()
            return row is not None
    except Exception:
        return False


requires_pg = pytest.mark.skipif(
    not pg_available(), reason="pgvector not available locally"
)
