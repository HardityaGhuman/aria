"""
rag/retriever.py
----------------
Hybrid retrieval: dense vector search + BM25 keyword search, fused with
Reciprocal Rank Fusion (RRF).
"""
from backend.core.config import (
    BM25_CANDIDATE_POOL,
    RETRIEVAL_TOP_K,
    RRF_K_CONSTANT,
)
from backend.rag.bm25 import get_bm25_index, tokenize_for_bm25
from backend.rag.embedding import get_embedding_function
from backend.rag.schema import RetrievedContext
from backend.rag.vector_store import get_collection

# Vector candidates beyond this cosine distance are treated as non-matches.
MAX_DISTANCE = 0.8
# A BM25 candidate must score at least this fraction of the best score for the
# query to enter the pool, so trivial single-token overlaps don't pollute results.
BM25_MIN_SCORE_RATIO = 0.15


def retrieve_context(query: str, n_results: int = None) -> RetrievedContext:
    """Hybrid BM25 + vector search over the indexed corpus, fused with RRF."""
    if n_results is None:
        n_results = RETRIEVAL_TOP_K

    collection = get_collection()
    if collection.count() == 0:
        return RetrievedContext(
            text="No company policy documents have been indexed yet.",
            sources=[],
        )

    # 1. Vector search. Embed the query with the same model used for the
    # documents and pass the vector explicitly; querying with query_texts would
    # make Chroma fall back to its own default embedder, which only works here by
    # coincidence and breaks silently if EMBEDDING_MODEL_NAME changes.
    query_embedding = get_embedding_function().embed_query(query)
    vec_results = collection.query(
        query_embeddings=[query_embedding],
        n_results=BM25_CANDIDATE_POOL,
        where={"content_type": {"$ne": "toc"}},
        include=["documents", "metadatas", "distances"],
    )

    vec_chunks = vec_results["documents"][0] if vec_results.get("documents") and vec_results["documents"] else []
    vec_metadatas = vec_results["metadatas"][0] if vec_results.get("metadatas") and vec_results["metadatas"] else []
    vec_distances = vec_results.get("distances", [[]])[0] if vec_results.get("distances") and vec_results["distances"] else []

    vec_valid = []
    chunk_dict = {}
    for chunk, meta, dist in zip(vec_chunks, vec_metadatas, vec_distances):
        if dist is not None and dist <= MAX_DISTANCE:
            chunk_id = f"{meta.get('source')}:{meta.get('chunk')}"
            vec_valid.append(chunk_id)
            chunk_dict[chunk_id] = {"chunk": chunk, "metadata": meta, "distance": dist}

    # 2. BM25 search.
    bm25_index, bm25_corpus, bm25_metadata = get_bm25_index()
    bm25_valid = []
    if bm25_index is not None:
        tokenized_query = tokenize_for_bm25(query)
        bm25_scores = bm25_index.get_scores(tokenized_query)
        scored_indices = sorted(enumerate(bm25_scores), key=lambda x: x[1], reverse=True)[:BM25_CANDIDATE_POOL]

        # Floor relative to the best score so chunks that merely share a generic
        # token (e.g. "types", "get") don't enter the pool as noise.
        top_score = scored_indices[0][1] if scored_indices else 0.0
        min_score = top_score * BM25_MIN_SCORE_RATIO

        for idx, score in scored_indices:
            if score > 0 and score >= min_score:
                meta = bm25_metadata[idx]
                chunk = bm25_corpus[idx]
                chunk_id = f"{meta.get('source')}:{meta.get('chunk')}"
                bm25_valid.append(chunk_id)
                if chunk_id not in chunk_dict:
                    chunk_dict[chunk_id] = {"chunk": chunk, "metadata": meta, "distance": None}

    # 3. RRF merge.
    all_candidates = set(vec_valid) | set(bm25_valid)
    if not all_candidates:
        return RetrievedContext(text="No relevant context found.", sources=[])

    rrf_scores = {}
    fallback_rank = BM25_CANDIDATE_POOL + 1
    for chunk_id in all_candidates:
        vec_rank = vec_valid.index(chunk_id) + 1 if chunk_id in vec_valid else fallback_rank
        bm25_rank = bm25_valid.index(chunk_id) + 1 if chunk_id in bm25_valid else fallback_rank

        score = (1 / (RRF_K_CONSTANT + vec_rank)) + (1 / (RRF_K_CONSTANT + bm25_rank))
        rrf_scores[chunk_id] = score

    sorted_chunks = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)[:n_results]

    # Order the selected chunks by (source, chunk) for stable, readable context.
    sorted_items = sorted(
        (chunk_dict[cid] for cid in sorted_chunks),
        key=lambda x: (x["metadata"].get("source", ""), x["metadata"].get("chunk", 0)),
    )

    formatted = []
    sources = []
    for item in sorted_items:
        chunk = item["chunk"]
        metadata = item["metadata"]
        distance = item["distance"]
        source = metadata.get("source", "Unknown source")
        chunk_number = metadata.get("chunk", "?")
        formatted.append(f"[Source: {source}, chunk {chunk_number}]\n{chunk}")
        sources.append({
            "source": source,
            "chunk": chunk_number,
            "distance": round(float(distance), 4) if distance is not None else None,
        })

    if not formatted:
        return RetrievedContext(text="No relevant context found.", sources=[])

    return RetrievedContext(text="\n\n".join(formatted), sources=sources)
