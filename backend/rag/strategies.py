"""
rag/strategies.py
-----------------
Retrieval strategies as interchangeable functions. Each takes a query and
returns a ranked list of ``Candidate`` chunks (best first):

    vector  — dense semantic search (cosine distance)
    bm25    — lexical keyword search
    hybrid  — vector + bm25 fused with Reciprocal Rank Fusion (RRF)

The ``STRATEGIES`` registry lets the retriever and the (future) evaluation
harness select a strategy by name.
"""
from dataclasses import replace

from backend.core.config import BM25_CANDIDATE_POOL, RETRIEVAL_MAX_DISTANCE, RRF_K_CONSTANT
from backend.rag.bm25 import get_bm25_index, tokenize_for_bm25
from backend.rag.embedding import get_embedding_function
from backend.rag.schema import Candidate
from backend.rag.vector_store import get_collection

# A BM25 candidate must score at least this fraction of the best score to enter
# the pool, so trivial single-token overlaps don't pollute results.
BM25_MIN_SCORE_RATIO = 0.15
# Structural chunk types that never carry answer payload: TOC pages and the
# leading doc-overview block. Excluded from every retrieval path.
_EXCLUDED_TYPES = ["toc", "overview"]


def _chunk_id(metadata: dict) -> str:
    return f"{metadata.get('source')}:{metadata.get('chunk')}"


def _vector_where(allowed_regions: list[str] | None = None) -> dict:
    """Chroma ``where``: drop structural chunks, always exclude superseded docs,
    and (when provided) restrict to the caller's regions. Tier gating now lives in
    the retriever (app-layer partition), not here."""
    filters: list[dict] = [
        {"content_type": {"$nin": _EXCLUDED_TYPES}},
        {"status": {"$ne": "superseded"}},
    ]
    if allowed_regions is not None:
        filters.append({"region": {"$in": allowed_regions}})
    return filters[0] if len(filters) == 1 else {"$and": filters}


def _region_allowed(meta: dict, allowed_regions: list[str] | None) -> bool:
    """Region gate for the BM25 path: global docs are visible to all regions."""
    if allowed_regions is None:
        return True
    return meta.get("region") in allowed_regions


def _status_active(meta: dict) -> bool:
    """Returns False only for documents explicitly marked as superseded."""
    return meta.get("status") != "superseded"


def vector_search(
    query: str,
    pool: int = BM25_CANDIDATE_POOL,
    allowed_regions: list[str] | None = None,
) -> list[Candidate]:
    """Dense semantic search. Embeds the query explicitly with the same model
    used for the documents (Chroma's default embedder would diverge silently if
    EMBEDDING_MODEL_NAME changed)."""
    collection = get_collection()
    query_embedding = get_embedding_function().embed_query(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=pool,
        where=_vector_where(allowed_regions),
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0] if results.get("documents") and results["documents"] else []
    metas = results["metadatas"][0] if results.get("metadatas") and results["metadatas"] else []
    dists = results.get("distances", [[]])[0] if results.get("distances") and results["distances"] else []

    candidates = []
    for chunk, meta, dist in zip(docs, metas, dists):
        if dist is not None and dist <= RETRIEVAL_MAX_DISTANCE:
            candidates.append(Candidate(_chunk_id(meta), chunk, meta, distance=dist))
    return candidates


def bm25_search(
    query: str,
    pool: int = BM25_CANDIDATE_POOL,
    allowed_regions: list[str] | None = None,
) -> list[Candidate]:
    """Lexical BM25 keyword search with a relative score floor."""
    index, corpus, metadata = get_bm25_index()
    if index is None:
        return []

    scores = index.get_scores(tokenize_for_bm25(query))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

    top_score = ranked[0][1] if ranked else 0.0
    min_score = top_score * BM25_MIN_SCORE_RATIO

    candidates = []
    for idx, score in ranked:
        if score <= 0 or score < min_score:
            continue
        meta = metadata[idx]
        # The BM25 index spans the whole corpus, so apply the same filters here
        # that the vector path applies via ``where``: structural exclusion,
        # superseded status, and region. Tier gating is handled by the retriever.
        if meta.get("content_type") in _EXCLUDED_TYPES:
            continue
        if not _status_active(meta):
            continue
        if not _region_allowed(meta, allowed_regions):
            continue
        candidates.append(Candidate(_chunk_id(meta), corpus[idx], meta, score=score))
        if len(candidates) >= pool:
            break
    return candidates


def hybrid_search(
    query: str,
    pool: int = BM25_CANDIDATE_POOL,
    allowed_regions: list[str] | None = None,
) -> list[Candidate]:
    """Fuse vector + BM25 rankings with Reciprocal Rank Fusion."""
    vec = vector_search(query, pool, allowed_regions)
    bm = bm25_search(query, pool, allowed_regions)

    vec_ids = [c.chunk_id for c in vec]
    bm_ids = [c.chunk_id for c in bm]

    # Prefer the vector candidate when a chunk appears in both (it carries the
    # cosine distance we surface to the UI).
    by_id: dict[str, Candidate] = {}
    for cand in vec:
        by_id.setdefault(cand.chunk_id, cand)
    for cand in bm:
        by_id.setdefault(cand.chunk_id, cand)

    fallback_rank = pool + 1
    fused = []
    for chunk_id, cand in by_id.items():
        vec_rank = vec_ids.index(chunk_id) + 1 if chunk_id in vec_ids else fallback_rank
        bm_rank = bm_ids.index(chunk_id) + 1 if chunk_id in bm_ids else fallback_rank
        score = (1 / (RRF_K_CONSTANT + vec_rank)) + (1 / (RRF_K_CONSTANT + bm_rank))
        fused.append(replace(cand, score=score))

    fused.sort(key=lambda c: c.score, reverse=True)
    return fused


STRATEGIES = {
    "vector": vector_search,
    "bm25": bm25_search,
    "hybrid": hybrid_search,
}
STRATEGY_NAMES = list(STRATEGIES)
