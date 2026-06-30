"""
rag/vector_store.py
-------------------
Chroma vector store wrapper (persistent, cosine distance).
"""
# pyrefly: ignore [missing-import]
from langchain_chroma import Chroma

from backend.core.config import CHROMA_DB_PATH
from backend.rag.embedding import get_embedding_function

_vector_store = None


def get_vector_store():
    global _vector_store
    if _vector_store is None:
        _vector_store = Chroma(
            collection_name="company_docs",
            embedding_function=get_embedding_function(),
            persist_directory=CHROMA_DB_PATH,
            # Cosine space so the retriever's RETRIEVAL_MAX_DISTANCE is a meaningful
            # similarity cutoff. Chroma defaults to squared-L2, whose distances
            # sit far above any reasonable threshold for these embeddings.
            collection_metadata={"hnsw:space": "cosine"},
        )
    return _vector_store


def get_collection():
    """Underlying Chroma collection, used by the hybrid retriever and indexer."""
    return get_vector_store()._collection


def indexed_sources() -> set[str]:
    """Return the distinct ``source`` values (paths relative to docs/) that have
    at least one chunk in the vector store.

    Why: the admin document list merges on-disk files with the ``document_status``
    table, but the seed corpus is built by the offline indexer, which never writes
    that table — so those docs report ``unknown`` despite being fully indexed. This
    asks Chroma what is actually indexed, the ground truth, so the UI can show
    ``indexed`` for them. One metadata scan; fine at this corpus size."""
    got = get_collection().get(include=["metadatas"])
    return {
        m["source"]
        for m in (got.get("metadatas") or [])
        if m and m.get("source")
    }
