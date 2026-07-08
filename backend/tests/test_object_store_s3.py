import uuid
from backend.rag.object_store import S3ObjectStore
from backend.tests.conftest_s3 import requires_s3


@requires_s3
def test_s3_roundtrip_and_presign():
    s = S3ObjectStore()
    key = f"originals/test-{uuid.uuid4()}"
    s.put(key, b"hello-s3", "text/plain")
    try:
        assert s.exists(key) is True
        assert s.get(key) == b"hello-s3"
        url = s.presigned_url(key, 60)
        assert key in url and url.startswith("http")
    finally:
        s.delete(key)
    assert s.exists(key) is False
