from backend.rag import vector_schema
from backend.tests.conftest_pg import requires_pg
from backend.core.db import connection


@requires_pg
def test_schema_creates_tables_and_vector_column():
    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    with connection() as conn:
        # extension installed
        assert conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname='vector'"
        ).fetchone() is not None
        # the three tables exist
        for table in ("documents", "document_versions", "chunks"):
            assert conn.execute(
                "SELECT to_regclass(%s)", (f"public.{table}",)
            ).fetchone()[0] is not None
        # chunks.embedding is a 384-dim vector
        typ = conn.execute("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            WHERE a.attrelid = 'public.chunks'::regclass AND a.attname='embedding'
        """).fetchone()[0]
        assert "vector(384)" in typ


@requires_pg
def test_initialize_is_idempotent():
    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    vector_schema.initialize_vector_store_schema()  # second call must not raise


@requires_pg
def test_document_versions_has_object_columns():
    vector_schema.ensure_vector_extension()
    vector_schema.initialize_vector_store_schema()
    with connection() as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='document_versions'"
        ).fetchall()}
    assert {"object_key", "original_content_type", "original_size"} <= cols
