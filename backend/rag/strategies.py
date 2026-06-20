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

from backend.core.config import BM25_CANDIDATE_POOL, RRF_K_CONSTANT
from backend.rag.bm25 import get_bm25_index, tokenize_for_bm25
from backend.rag.embedding import get_embedding_function
from backend.rag.schema import Candidate
from backend.rag.vector_store import get_collection

# Vector candidates beyond this cosine distance are treated as non-matches.
MAX_DISTANCE = 0.8
# A BM25 candidate must score at least this fraction of the best score to enter
# the pool, so trivial single-token overlaps don't pollute results.
BM25_MIN_SCORE_RATIO = 0.15


def _chunk_id(metadata: dict) -> str:
    return f"{metadata.get('source')}:{metadata.get('chunk')}"


def vector_search(query: str, pool: int = BM25_CANDIDATE_POOL) -> list[Candidate]:
    """Dense semantic search. Embeds the query explicitly with the same model
    used for the documents (Chroma's default embedder would diverge silently if
    EMBEDDING_MODEL_NAME changed)."""
    collection = get_collection()
    query_embedding = get_embedding_function().embed_query(query)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=pool,
        where={"content_type": {"$ne": "toc"}},
        include=["documents", "metadatas", "distances"],
    )

    docs = results["documents"][0] if results.get("documents") and results["documents"] else []
    metas = results["metadatas"][0] if results.get("metadatas") and results["metadatas"] else []
    dists = results.get("distances", [[]])[0] if results.get("distances") and results["distances"] else []

    candidates = []
    for chunk, meta, dist in zip(docs, metas, dists):
        if dist is not None and dist <= MAX_DISTANCE:
            candidates.append(Candidate(_chunk_id(meta), chunk, meta, distance=dist))
    return candidates


def bm25_search(query: str, pool: int = BM25_CANDIDATE_POOL) -> list[Candidate]:
    """Lexical BM25 keyword search with a relative score floor."""
    index, corpus, metadata = get_bm25_index()
    if index is None:
        return []

    scores = index.get_scores(tokenize_for_bm25(query))
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:pool]

    top_score = ranked[0][1] if ranked else 0.0
    min_score = top_score * BM25_MIN_SCORE_RATIO

    candidates = []
    for idx, score in ranked:
        if score > 0 and score >= min_score:
            meta = metadata[idx]
            candidates.append(Candidate(_chunk_id(meta), corpus[idx], meta, score=score))
    return candidates


def hybrid_search(query: str, pool: int = BM25_CANDIDATE_POOL) -> list[Candidate]:
    """Fuse vector + BM25 rankings with Reciprocal Rank Fusion."""
    vec = vector_search(query, pool)
    bm = bm25_search(query, pool)

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
