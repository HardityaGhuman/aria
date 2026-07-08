"""rag/object_store.py
------------------
Private object storage seam (§11 slice C) for ORIGINAL uploaded files. Callers
talk to an `ObjectStore`, never to boto3. Two impls: `S3ObjectStore` (prod, real
AWS S3) and `InMemoryObjectStore` (hermetic tests). Keys are opaque
`originals/<version_id>`; we store keys + metadata, never public URLs or paths.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.core.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def presigned_url(self, key: str, ttl_seconds: int) -> str: ...


class InMemoryObjectStore:
    """Test fake. Holds bytes + content_type per key. No network."""

    def __init__(self) -> None:
        self._blobs: dict[str, tuple[bytes, str | None]] = {}

    def put(self, key: str, data: bytes, content_type: str | None) -> None:
        self._blobs[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        return self._blobs[key][0]  # KeyError if absent (mirrors a 404)

    def delete(self, key: str) -> None:
        self._blobs.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._blobs

    def presigned_url(self, key: str, ttl_seconds: int) -> str:
        return f"memory://{key}"


class S3ObjectStore:
    """Real AWS S3 (boto3). boto3 imported lazily so the module imports without
    the dependency present; only constructing this impl needs it."""

    def __init__(self) -> None:
        import boto3  # lazy: not needed for InMemory tests
        from backend.core.config import (
            S3_ENDPOINT_URL, S3_BUCKET, S3_REGION, S3_ACCESS_KEY, S3_SECRET_KEY,
        )
        if not S3_BUCKET:
            raise RuntimeError("OBJECT_STORE_BACKEND=s3 but S3_BUCKET is unset.")
        self._bucket = S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL or None,  # empty -> real AWS
            region_name=S3_REGION,
            aws_access_key_id=S3_ACCESS_KEY or None,
            aws_secret_access_key=S3_SECRET_KEY or None,
        )

    def put(self, key: str, data: bytes, content_type: str | None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def presigned_url(self, key: str, ttl_seconds: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=ttl_seconds,
        )

    def ensure_bucket(self) -> None:
        """Verify the bucket is reachable + authorized (fail-fast at startup).
        Does NOT create — the least-privilege IAM key lacks CreateBucket."""
        from botocore.exceptions import ClientError
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as e:
            raise RuntimeError(
                f"S3 bucket '{self._bucket}' unreachable/unauthorized: {e}. "
                "Check S3_BUCKET/S3_REGION/creds and that the bucket exists."
            ) from e


_object_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Process-wide object-store singleton, built from OBJECT_STORE_BACKEND."""
    global _object_store
    if _object_store is None:
        from backend.core.config import OBJECT_STORE_BACKEND
        _object_store = (
            InMemoryObjectStore() if OBJECT_STORE_BACKEND == "memory"
            else S3ObjectStore()
        )
    return _object_store


def set_object_store(store: ObjectStore | None) -> None:
    """Test seam: inject a fake (or None to reset)."""
    global _object_store
    _object_store = store
