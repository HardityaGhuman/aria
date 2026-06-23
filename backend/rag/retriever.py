"""
rag/retriever.py
----------------
Runs a retrieval strategy and formats the result into a ``RetrievedContext``
(the context block + per-chunk source metadata) for the answer step.
"""
from backend.core.config import RETRIEVAL_STRATEGY, RETRIEVAL_TOP_K
from backend.rag.schema import RetrievedContext
from backend.rag.strategies import STRATEGIES
from backend.rag.vector_store import get_collection


def retrieve(query: str, strategy: str = None, n_results: int = None) -> RetrievedContext:
    """Retrieve context for ``query`` using the named strategy.

    Args:
        strategy:  "vector", "bm25", or "hybrid" (defaults to RETRIEVAL_STRATEGY).
        n_results: number of chunks to return (defaults to RETRIEVAL_TOP_K).
    """
    strategy = strategy or RETRIEVAL_STRATEGY
    if n_results is None:
        n_results = RETRIEVAL_TOP_K
    if strategy not in STRATEGIES:
        raise ValueError(
            f"Unknown retrieval strategy '{strategy}'. Choose from {list(STRATEGIES)}."
        )

    if get_collection().count() == 0:
        return RetrievedContext("No company policy documents have been indexed yet.")

    candidates = STRATEGIES[strategy](query)
    if not candidates:
        return RetrievedContext("No relevant context found.")

    # Order the selected chunks by (source, chunk) for stable, readable context.
    top = sorted(
        candidates[:n_results],
        key=lambda c: (c.metadata.get("source", ""), c.metadata.get("chunk", 0)),
    )

    formatted = []
    sources = []
    for cand in top:
        source = cand.metadata.get("source", "Unknown source")
        chunk_number = cand.metadata.get("chunk", "?")
        formatted.append(f"[Source: {source}, chunk {chunk_number}]\n{cand.text}")
        sources.append({
            "source": source,
            "chunk": chunk_number,
            "department": cand.metadata.get("department") or None,
            "access_tier": cand.metadata.get("access_tier") or None,
            "section": cand.metadata.get("parent_section") or None,
            "distance": round(float(cand.distance), 4) if cand.distance is not None else None,
        })

    return RetrievedContext("\n\n".join(formatted), sources)


def retrieve_context(query: str, n_results: int = None) -> RetrievedContext:
    """Backwards-compatible entry point using the configured default strategy."""
    return retrieve(query, n_results=n_results)
