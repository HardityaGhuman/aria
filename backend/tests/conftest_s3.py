"""tests/conftest_s3.py
--------------------
Skip helper for live-S3 tests. The default suite is hermetic; any test that
touches a real bucket is `@requires_s3` and skips unless S3 creds + bucket are
configured AND the bucket is reachable."""
import pytest


def s3_available() -> bool:
    try:
        from backend.core.config import OBJECT_STORE_BACKEND, S3_BUCKET, S3_ACCESS_KEY
        if OBJECT_STORE_BACKEND != "s3" or not (S3_BUCKET and S3_ACCESS_KEY):
            return False
        from backend.rag.object_store import S3ObjectStore
        S3ObjectStore().ensure_bucket()
        return True
    except Exception:
        return False


requires_s3 = pytest.mark.skipif(
    not s3_available(), reason="live S3 bucket/creds not configured"
)
