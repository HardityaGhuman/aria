import pytest
from backend.rag.object_store import InMemoryObjectStore


def test_put_get_roundtrip():
    s = InMemoryObjectStore()
    s.put("originals/1", b"hello", "text/plain")
    assert s.get("originals/1") == b"hello"


def test_exists_and_delete():
    s = InMemoryObjectStore()
    assert s.exists("originals/1") is False
    s.put("originals/1", b"x", None)
    assert s.exists("originals/1") is True
    s.delete("originals/1")
    assert s.exists("originals/1") is False


def test_get_missing_raises():
    s = InMemoryObjectStore()
    with pytest.raises(KeyError):
        s.get("nope")


def test_presigned_url_is_sentinel():
    s = InMemoryObjectStore()
    s.put("originals/1", b"x", None)
    assert s.presigned_url("originals/1", 60) == "memory://originals/1"
