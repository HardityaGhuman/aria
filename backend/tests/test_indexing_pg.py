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
