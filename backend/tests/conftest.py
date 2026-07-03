"""Shared test setup. Sets a JWT secret before any backend module imports config,
so token signing/verification works in tests without a real .env secret."""
import os

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-0123456789abcdef")


@pytest.fixture(scope="session", autouse=True)
def _close_db_pool():
    """Close the shared connection pool at the end of the test session so its
    background worker threads stop cleanly (otherwise psycopg_pool warns about
    threads it couldn't join at interpreter exit)."""
    yield
    from backend.core.db import close_pool
    close_pool()
