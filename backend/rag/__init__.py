"""
rag package
-----------
Structure-aware, hybrid (BM25 + vector) retrieval over policy documents.

Public API is re-exported here so callers can simply::

    from backend.rag import retrieve_context, initialize_vectorstore
"""
from backend.rag.indexing import initialize_vectorstore, list_policy_documents
from backend.rag.query_rewriter import rewrite_query
from backend.rag.retriever import retrieve, retrieve_context
from backend.rag.schema import Candidate, RetrievedContext
from backend.rag.strategies import STRATEGY_NAMES
from backend.rag.vector_store import get_collection

__all__ = [
    "Candidate",
    "RetrievedContext",
    "get_collection",
    "initialize_vectorstore",
    "list_policy_documents",
    "retrieve",
    "retrieve_context",
    "rewrite_query",
    "STRATEGY_NAMES",
]
