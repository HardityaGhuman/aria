import backend.rag.indexing as indexing
from backend.rag import vector_schema
from backend.rag.vector_repository import PgVectorRepository, get_repository, set_repository
from backend.tests.conftest_pg import requires_pg


@requires_pg
def test_reindex_writes_pg_and_skips_unchanged(monkeypatch, tmp_path):
    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    set_repository(PgVectorRepository())
    repo = get_repository()
    repo.delete_document("hr/leave.md")

    # a tiny corpus: one hr doc
    (tmp_path / "hr").mkdir()
    (tmp_path / "hr" / "leave.md").write_text(
        "---\naccess_tier: all\nregion: global\n---\n# Leave\nEmployees get 20 PTO days.\n"
    )
    monkeypatch.setattr(indexing, "DOCS_PATH", str(tmp_path))

    first = indexing.initialize_vectorstore()
    assert first["indexed"] >= 1
    assert "hr/leave.md" in repo.indexed_sources()

    second = indexing.initialize_vectorstore()
    assert second["skipped"] >= 1 and second["indexed"] == 0   # unchanged → skipped

    repo.delete_document("hr/leave.md")
    set_repository(None)


@requires_pg
def test_indexing_stores_original_for_active_version(monkeypatch, tmp_path):
    from backend.rag.object_store import InMemoryObjectStore, set_object_store
    store = InMemoryObjectStore()
    set_object_store(store)

    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    set_repository(PgVectorRepository())
    repo = get_repository()
    repo.delete_document("hr/sample.md")

    (tmp_path / "hr").mkdir()
    (tmp_path / "hr" / "sample.md").write_text(
        "---\naccess_tier: all\nregion: global\n---\n# Leave\nEmployees get 20 days leave.\n"
    )
    monkeypatch.setattr(indexing, "DOCS_PATH", str(tmp_path))

    stats = indexing.initialize_vectorstore()
    assert stats["indexed"] >= 1

    from backend.core.db import connection
    with connection() as conn:
        row = conn.execute(
            "SELECT v.object_key, v.original_size FROM documents d "
            "JOIN document_versions v ON d.active_version_id=v.version_id "
            "WHERE d.document_id=%s", ("hr/sample.md",)).fetchone()
    assert row is not None and row[0].startswith("originals/") and row[1] > 0
    assert store.exists(row[0]) is True

    repo.delete_document("hr/sample.md")
    set_repository(None)
