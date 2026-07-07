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
