"""rag/vector_schema.py
--------------------
Authoritative pgvector store DDL (§11 B). One idempotent bootstrap: the `vector`
extension, the documents/document_versions/chunks tables, and their indexes. Run
at startup, same pattern as the other `initialize_*_table` helpers.
"""
import os

from backend.core.db import connection, get_pool
from backend.core.logging import get_logger

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    document_id       TEXT PRIMARY KEY,
    department        TEXT,
    active_version_id BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_versions (
    version_id        BIGSERIAL PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    version_no        INT NOT NULL,
    lifecycle_state   TEXT NOT NULL DEFAULT 'active',
    parser_version    TEXT,
    embedding_version TEXT,
    checksum          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_no)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       BIGSERIAL PRIMARY KEY,
    version_id     BIGINT NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
    document_id    TEXT NOT NULL,
    chunk_index    INT NOT NULL,
    content        TEXT NOT NULL,
    embedding      vector(384) NOT NULL,
    metadata       JSONB NOT NULL,
    region         TEXT,
    content_status TEXT,
    content_type   TEXT
);

CREATE INDEX IF NOT EXISTS chunks_doc_idx     ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_version_idx ON chunks (version_id);
CREATE INDEX IF NOT EXISTS chunks_region_idx  ON chunks (region);
CREATE INDEX IF NOT EXISTS chunks_status_idx  ON chunks (content_status);
CREATE INDEX IF NOT EXISTS chunks_ctype_idx   ON chunks (content_type);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);
"""


def ensure_vector_extension() -> None:
    """Create the pgvector extension. Fail fast with a clear message if the
    extension binary isn't installed (same stance as a missing JWT secret)."""
    with connection() as conn:
        available = conn.execute(
            "SELECT 1 FROM pg_available_extensions WHERE name='vector'"
        ).fetchone()
        if available is None:
            raise RuntimeError(
                "pgvector extension is not installed in this Postgres. "
                "Install it (e.g. `brew install pgvector`) and restart."
            )
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Re-register the type on all pooled connections now the extension exists,
    # so any connection opened before CREATE EXTENSION also adapts `vector`.
    from pgvector.psycopg import register_vector  # pyrefly: ignore [missing-import]
    for _ in range(int(os.getenv("DB_POOL_MAX_SIZE", "10"))):
        with connection() as conn:
            register_vector(conn)
    get_pool()  # ensure the pool is live (no-op if already open)


def initialize_vector_store_schema() -> None:
    """Create the tables + indexes, idempotently."""
    with connection() as conn:
        conn.execute(_DDL)
    logger.info("Vector store schema (documents/document_versions/chunks) ready.")
