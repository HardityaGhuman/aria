"""
core/db.py
----------
One process-wide psycopg connection pool.

Every core module used to call ``psycopg.connect(DATABASE_URL)`` per request and
rely on ``with conn:`` (a *transaction* context — it never closes the
connection), so connections accumulated until GC. Under any real concurrency
that exhausts Postgres' connection slots and pays a fresh TCP+auth handshake on
every query. A pool bounds the open connections and reuses warm ones: checking a
connection back in on ``with`` exit instead of opening a new one each call.

Modules keep their thin ``_connect()`` seam (now a context manager over this
pool), so call sites — ``with _connect() as connection:`` — are unchanged.
"""
import os
from contextlib import contextmanager

# pyrefly: ignore [missing-import]
import psycopg
# pyrefly: ignore [missing-import]
from psycopg_pool import ConnectionPool, PoolTimeout

from backend.core.config import DATABASE_URL

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """The process-wide pool, opened lazily on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
            # Recycle idle connections so a serverless/proxy idle-timeout can't
            # hand back a dead socket.
            max_idle=float(os.getenv("DB_POOL_MAX_IDLE", "300")),
            open=True,
        )
    return _pool


@contextmanager
def connection():
    """Check a connection out of the pool for the duration of the block. The
    pool commits the transaction (or rolls back on exception) and returns the
    connection on exit — it is not closed."""
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def pooled(error_factory):
    """``connection()`` that maps connection-level failures (pool exhaustion, a
    dropped socket) to a module's typed error — preserving the old ``_connect()``
    contract so callers keep raising e.g. ``ChatMemoryError`` on a DB outage.
    Programming errors (bad SQL) propagate raw.

    Usage:  ``def _connect(): return db.pooled(lambda: MyError("..."))``
    """
    try:
        with connection() as conn:
            yield conn
    except (psycopg.OperationalError, PoolTimeout) as exc:
        raise error_factory() from exc


def close_pool() -> None:
    """Close the pool (clean shutdown / test teardown). Safe if never opened."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
