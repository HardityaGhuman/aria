"""
rag package
-----------
Structure-aware, hybrid (BM25 + vector) retrieval over policy documents.

Public API is re-exported here so callers can simply::

    from backend.rag import retrieve_context, initialize_vectorstore
"""
from backend.rag.indexing import (
    delete_document_chunks,
    initialize_vectorstore,
    list_policy_documents,
)
from backend.rag.query_rewriter import rewrite_query
from backend.rag.retriever import retrieve, retrieve_context
from backend.rag.schema import Candidate, RetrievedContext
from backend.rag.strategies import STRATEGY_NAMES
from backend.rag.vector_store import get_collection, indexed_sources

__all__ = [
    "Candidate",
    "RetrievedContext",
    "delete_document_chunks",
    "get_collection",
    "indexed_sources",
    "initialize_vectorstore",
    "list_policy_documents",
    "retrieve",
    "retrieve_context",
    "rewrite_query",
    "STRATEGY_NAMES",
]
