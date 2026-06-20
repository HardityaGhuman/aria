"""
rag package
-----------
Structure-aware, hybrid (BM25 + vector) retrieval over policy documents.

Public API is re-exported here so callers can simply::

    from backend.rag import retrieve_context, initialize_vectorstore
"""
from backend.rag.indexing import initialize_vectorstore, list_policy_documents
from backend.rag.retriever import retrieve_context
from backend.rag.schema import RetrievedContext
from backend.rag.vector_store import get_collection

__all__ = [
    "RetrievedContext",
    "get_collection",
    "initialize_vectorstore",
    "list_policy_documents",
    "retrieve_context",
]
