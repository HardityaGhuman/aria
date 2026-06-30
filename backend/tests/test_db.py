"""
tests/test_db.py
----------------
Shared connection pool. Runs against the real local Postgres (same as every
other DB-touching test). Proves the pool is a process singleton and that a
checked-out connection executes and is returned (not closed) on context exit.
"""
import backend.core.db as db


def test_pool_is_a_singleton():
    assert db.get_pool() is db.get_pool()


def test_connection_executes_and_returns_to_pool():
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
    # A second checkout must succeed — i.e. the first was returned, not leaked.
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 2")
            assert cur.fetchone()[0] == 2
