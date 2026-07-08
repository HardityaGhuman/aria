"""rag/vector_repository.py
------------------------
The single storage seam (§11 A). Retrieval strategies, BM25, the retriever, and
the indexer talk to a `VectorRepository`, never to a concrete store. Two impls:
`PgVectorRepository` (prod, Postgres+pgvector) and `InMemoryVectorRepository`
(tests — cosine in Python, no live DB).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from backend.rag.schema import Candidate


@dataclass
class ChunkInput:
    document_id: str
    chunk_index: int
    content: str
    embedding: list[float]
    metadata: dict
    region: str | None
    content_status: str | None
    content_type: str | None


def _chunk_id(metadata: dict) -> str:
    return f"{metadata.get('source')}:{metadata.get('chunk')}"


@runtime_checkable
class VectorRepository(Protocol):
    def query(self, embedding: list[float], k: int,
              allowed_regions: list[str] | None,
              exclude_content_types: list[str]) -> list[Candidate]: ...
    def all_chunks(self, exclude_content_types: list[str]) -> list[Candidate]: ...
    def count(self) -> int: ...
    def indexed_sources(self) -> set[str]: ...
    def upsert_document(self, document_id: str, department: str | None, checksum: str,
                        parser_version: str, embedding_version: str,
                        chunks: list[ChunkInput]) -> None: ...
    def delete_document(self, document_id: str) -> int: ...
    def active_version_meta(self, document_id: str) -> tuple[str | None, str | None]: ...


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


class InMemoryVectorRepository:
    """Test fake. Holds ChunkInputs per document + the active version's
    (checksum, parser_version). Mirrors PgVectorRepository semantics."""

    def __init__(self) -> None:
        self._docs: dict[str, list[ChunkInput]] = {}
        self._meta: dict[str, tuple[str, str]] = {}  # document_id -> (checksum, parser_version)

    def upsert_document(self, document_id, department, checksum, parser_version,
                        embedding_version, chunks):
        self._docs[document_id] = list(chunks)
        self._meta[document_id] = (checksum, parser_version)

    def delete_document(self, document_id) -> int:
        n = len(self._docs.get(document_id, []))
        self._docs.pop(document_id, None)
        self._meta.pop(document_id, None)
        return n

    def active_version_meta(self, document_id):
        return self._meta.get(document_id, (None, None))

    def count(self) -> int:
        return sum(len(v) for v in self._docs.values())

    def indexed_sources(self) -> set[str]:
        return {ci.metadata.get("source") for chunks in self._docs.values()
                for ci in chunks if ci.metadata.get("source")}

    def _iter(self):
        for chunks in self._docs.values():
            yield from chunks

    def all_chunks(self, exclude_content_types) -> list[Candidate]:
        out = []
        for ci in self._iter():
            if (ci.content_type or "") in exclude_content_types:
                continue
            out.append(Candidate(_chunk_id(ci.metadata), ci.content, ci.metadata))
        return out

    def query(self, embedding, k, allowed_regions, exclude_content_types) -> list[Candidate]:
        scored = []
        for ci in self._iter():
            if (ci.content_type or "") in exclude_content_types:
                continue
            if (ci.content_status or "active") == "superseded":
                continue
            if allowed_regions is not None and ci.region not in allowed_regions:
                continue
            dist = _cosine_distance(embedding, ci.embedding)
            scored.append(Candidate(_chunk_id(ci.metadata), ci.content, ci.metadata, distance=dist))
        scored.sort(key=lambda c: c.distance)
        return scored[:k]


from backend.core.db import connection
from backend.core.config import RETRIEVAL_MAX_DISTANCE


class PgVectorRepository:
    """Postgres + pgvector implementation. All security filters (region, status,
    content_type, active-version) run in SQL; tier partition stays in the retriever."""

    def query(self, embedding, k, allowed_regions, exclude_content_types):
        sql = """
            SELECT c.content, c.metadata, (c.embedding <=> %(emb)s::vector) AS distance
            FROM chunks c
            JOIN document_versions v ON c.version_id = v.version_id
            WHERE v.lifecycle_state = 'active'
              AND (c.content_status IS DISTINCT FROM 'superseded')
              AND (%(exclude)s::text[] IS NULL OR NOT (c.content_type = ANY(%(exclude)s)))
              AND (%(regions)s::text[] IS NULL OR c.region = ANY(%(regions)s))
            ORDER BY distance
            LIMIT %(k)s
        """
        params = {
            "emb": embedding,
            "exclude": exclude_content_types or None,
            "regions": allowed_regions,
            "k": k,
        }
        with connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        out = []
        for content, metadata, distance in rows:
            if distance is not None and distance <= RETRIEVAL_MAX_DISTANCE:
                out.append(Candidate(_chunk_id(metadata), content, metadata, distance=float(distance)))
        return out

    def all_chunks(self, exclude_content_types):
        sql = """
            SELECT c.content, c.metadata
            FROM chunks c JOIN document_versions v ON c.version_id = v.version_id
            WHERE v.lifecycle_state = 'active'
              AND (%(exclude)s::text[] IS NULL OR NOT (c.content_type = ANY(%(exclude)s)))
        """
        with connection() as conn:
            rows = conn.execute(sql, {"exclude": exclude_content_types or None}).fetchall()
        return [Candidate(_chunk_id(m), content, m) for content, m in rows]

    def count(self):
        with connection() as conn:
            row = conn.execute("""
                SELECT count(*) FROM chunks c
                JOIN document_versions v ON c.version_id = v.version_id
                WHERE v.lifecycle_state = 'active'
            """).fetchone()
        return int(row[0])

    def indexed_sources(self):
        with connection() as conn:
            rows = conn.execute("""
                SELECT DISTINCT c.document_id FROM chunks c
                JOIN document_versions v ON c.version_id = v.version_id
                WHERE v.lifecycle_state = 'active'
            """).fetchall()
        return {r[0] for r in rows}

    def active_version_meta(self, document_id):
        with connection() as conn:
            row = conn.execute("""
                SELECT v.checksum, v.parser_version
                FROM documents d JOIN document_versions v ON d.active_version_id = v.version_id
                WHERE d.document_id = %s
            """, (document_id,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def delete_document(self, document_id) -> int:
        with connection() as conn:
            n = conn.execute(
                "SELECT count(*) FROM chunks WHERE document_id = %s", (document_id,)
            ).fetchone()[0]
            # ON DELETE CASCADE removes versions + chunks.
            conn.execute("DELETE FROM documents WHERE document_id = %s", (document_id,))
        return int(n)

    def upsert_document(self, document_id, department, checksum, parser_version,
                        embedding_version, chunks):
        with connection() as conn:
            # Atomic replace: drop the old doc (cascades versions+chunks), reinsert.
            conn.execute("DELETE FROM documents WHERE document_id = %s", (document_id,))
            conn.execute(
                "INSERT INTO documents (document_id, department) VALUES (%s, %s)",
                (document_id, department),
            )
            version_id = conn.execute("""
                INSERT INTO document_versions
                    (document_id, version_no, lifecycle_state, parser_version, embedding_version, checksum)
                VALUES (%s, 1, 'active', %s, %s, %s)
                RETURNING version_id
            """, (document_id, parser_version, embedding_version, checksum)).fetchone()[0]
            conn.execute(
                "UPDATE documents SET active_version_id = %s, updated_at = now() WHERE document_id = %s",
                (version_id, document_id),
            )
            from psycopg.types.json import Jsonb  # pyrefly: ignore [missing-import]
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO chunks
                        (version_id, document_id, chunk_index, content, embedding,
                         metadata, region, content_status, content_type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, [
                    (version_id, document_id, ci.chunk_index, ci.content, ci.embedding,
                     Jsonb(ci.metadata), ci.region, ci.content_status, ci.content_type)
                    for ci in chunks
                ])


_repository: VectorRepository | None = None


def get_repository() -> VectorRepository:
    """Process-wide repository singleton (prod: PgVectorRepository)."""
    global _repository
    if _repository is None:
        _repository = PgVectorRepository()
    return _repository


def set_repository(repo: VectorRepository | None) -> None:
    """Test seam: inject a fake (or None to reset)."""
    global _repository
    _repository = repo


def indexed_sources() -> set[str]:
    """Distinct source paths (rel docs/) with at least one active chunk — the
    admin doc-status ground truth. Module-level wrapper over the active repository
    so callers import a stable name (was `vector_store.indexed_sources`)."""
    return get_repository().indexed_sources()
