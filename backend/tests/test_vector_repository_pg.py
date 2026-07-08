from backend.rag import vector_schema
from backend.rag.vector_repository import PgVectorRepository, ChunkInput
from backend.tests.conftest_pg import requires_pg


def _setup():
    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    repo = PgVectorRepository()
    repo.delete_document("test/a.md")
    repo.delete_document("test/old.md")
    return repo


def _emb(v):
    e = [0.0] * 384
    e[0], e[1], e[2] = float(v[0]), float(v[1]), float(v[2])
    return e


def _chunk(source, idx, v, region="global", status="active", ctype=""):
    meta = {"source": source, "chunk": idx, "region": region, "status": status,
            "content_type": ctype, "access_tier": "all", "department": "test"}
    return ChunkInput(source, idx, f"{source}#{idx}", _emb(v), meta, region, status, ctype)


@requires_pg
def test_pg_query_ranks_and_filters():
    repo = _setup()
    repo.upsert_document("test/a.md", "test", "h1", "cv1", "ev1", [
        _chunk("test/a.md", 1, (1, 0, 0)),
        _chunk("test/a.md", 2, (0, 1, 0)),
    ])
    repo.upsert_document("test/old.md", "test", "h2", "cv1", "ev1", [
        _chunk("test/old.md", 1, (1, 0, 0), status="superseded"),
    ])
    got = repo.query(_emb((1, 0, 0)), 10, ["global"], ["toc", "overview"])
    ids = [c.chunk_id for c in got]
    assert ids[0] == "test/a.md:1"
    assert "test/old.md:1" not in ids
    assert got[0].metadata["access_tier"] == "all"   # JSONB roundtrip
    repo.delete_document("test/a.md"); repo.delete_document("test/old.md")


@requires_pg
def test_pg_upsert_stores_original_blob():
    from backend.rag.object_store import InMemoryObjectStore, set_object_store
    store = InMemoryObjectStore()
    set_object_store(store)
    repo = _setup()
    repo.upsert_document("test/a.md", "test", "h1", "cv1", "ev1",
                         [_chunk("test/a.md", 1, (1, 0, 0))],
                         original_bytes=b"PDFBYTES", original_content_type="application/pdf")
    from backend.core.db import connection
    with connection() as conn:
        row = conn.execute(
            "SELECT v.object_key, v.original_content_type, v.original_size "
            "FROM documents d JOIN document_versions v ON d.active_version_id=v.version_id "
            "WHERE d.document_id=%s", ("test/a.md",)).fetchone()
    assert row is not None
    key, ct, size = row
    assert key.startswith("originals/") and ct == "application/pdf" and size == 8
    assert store.get(key) == b"PDFBYTES"
    repo.delete_document("test/a.md")
    assert store.exists(key) is False   # delete_document removed the blob


@requires_pg
def test_pg_upsert_replaces_and_active_meta():
    repo = _setup()
    repo.upsert_document("test/a.md", "test", "h1", "cv1", "ev1", [_chunk("test/a.md", 1, (1, 0, 0))])
    repo.upsert_document("test/a.md", "test", "h2", "cv1", "ev1", [_chunk("test/a.md", 1, (0, 1, 0))])
    assert repo.count() >= 1
    assert repo.active_version_meta("test/a.md") == ("h2", "cv1")
    assert repo.indexed_sources() >= {"test/a.md"}
    repo.delete_document("test/a.md")
