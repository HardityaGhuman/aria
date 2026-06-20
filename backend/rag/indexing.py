"""
rag/indexing.py
---------------
Offline indexing: load policy documents, chunk them, and (re)build the Chroma
index. Changed files are detected by content hash; a chunk-logic change is
detected by CHUNK_VERSION. Run via ``python -m backend.index_documents``.
"""
import os
import re

from backend.core.config import DOCS_PATH
from backend.core.logging import get_logger
from backend.rag.bm25 import invalidate_bm25
from backend.rag.chunking import CHUNK_VERSION, chunk_documents
from backend.rag.loaders import SUPPORTED_EXTENSIONS, file_hash, load_document
from backend.rag.vector_store import get_collection, get_vector_store

logger = get_logger(__name__)


def list_policy_documents() -> list[dict]:
    if not os.path.exists(DOCS_PATH):
        return []

    documents = []
    for filename in sorted(os.listdir(DOCS_PATH)):
        filepath = os.path.join(DOCS_PATH, filename)
        extension = os.path.splitext(filename)[1].lower()
        if os.path.isfile(filepath) and extension in SUPPORTED_EXTENSIONS:
            documents.append({
                "filename": filename,
                "size_bytes": os.path.getsize(filepath),
                "type": extension.lstrip("."),
            })
    return documents


def initialize_vectorstore() -> dict:
    """Ingest policy documents into Chroma, reindexing changed files only."""
    collection = get_collection()
    stats = {"indexed": 0, "skipped": 0, "deleted": 0}

    if not os.path.exists(DOCS_PATH):
        os.makedirs(DOCS_PATH, exist_ok=True)
        logger.warning("Created docs folder at %s. Add PDF policy files there.", DOCS_PATH)
        return stats

    for filename in sorted(os.listdir(DOCS_PATH)):
        filepath = os.path.join(DOCS_PATH, filename)
        extension = os.path.splitext(filename)[1].lower()
        if not os.path.isfile(filepath) or extension not in SUPPORTED_EXTENSIONS:
            continue

        source_hash = file_hash(filepath)
        existing_chunks = collection.get(where={"source": filename})
        existing_hashes = {
            metadata.get("source_hash")
            for metadata in existing_chunks.get("metadatas", [])
            if metadata
        }
        existing_chunk_versions = {
            metadata.get("chunk_version")
            for metadata in existing_chunks.get("metadatas", [])
            if metadata
        }

        if (
            existing_chunks.get("ids")
            and existing_hashes == {source_hash}
            and existing_chunk_versions == {CHUNK_VERSION}
        ):
            logger.info("Skipping %s (already indexed)", filename)
            stats["skipped"] += 1
            continue

        if existing_chunks.get("ids"):
            collection.delete(ids=existing_chunks["ids"])
            stats["deleted"] += len(existing_chunks["ids"])

        pages = load_document(filepath)
        chunk_docs = chunk_documents(pages)
        if not chunk_docs:
            logger.warning("Skipping %s (no extractable text)", filename)
            stats["skipped"] += 1
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
        chunk_ids = [f"{safe_name}:{source_hash[:12]}:{i}" for i in range(len(chunk_docs))]

        documents = [doc.page_content for doc in chunk_docs]
        metadatas = [
            {
                "source": filename,
                "source_hash": source_hash,
                "chunk": i + 1,
                "chunk_version": CHUNK_VERSION,
                "type": extension.lstrip("."),
                "content_type": doc.metadata.get("content_type", ""),
                "parent_section": doc.metadata.get("parent_section", ""),
            }
            for i, doc in enumerate(chunk_docs)
        ]

        get_vector_store().add_texts(texts=documents, ids=chunk_ids, metadatas=metadatas)
        stats["indexed"] += len(documents)
        logger.info("Indexed %s (%d chunks)", filename, len(documents))

    if stats["indexed"] > 0 or stats["deleted"] > 0:
        invalidate_bm25()

    return stats
