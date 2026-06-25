"""
rag/indexing.py
---------------
Offline indexing: load policy documents, chunk them, and (re)build the Chroma
index. Documents live in department subfolders under ``DOCS_PATH`` and are
discovered by a recursive walk. Changed files are detected by content hash; a
chunk-logic change is detected by CHUNK_VERSION. Each chunk is tagged with the
document's ``department`` and ``access_tier`` so retrieval can later filter by
role. Run via ``python -m backend.index_documents``.
"""
import os
import re

from backend.core.config import DOCS_PATH
from backend.core.logging import get_logger
from backend.rag.bm25 import invalidate_bm25
from backend.rag.chunking import CHUNK_VERSION, chunk_documents
from backend.rag.loaders import (
    DEFAULT_ACCESS_TIER,
    DEFAULT_STATUS,
    SUPPORTED_EXTENSIONS,
    file_hash,
    load_document,
)
from backend.rag.vector_store import get_collection, get_vector_store

logger = get_logger(__name__)


def _iter_document_files(root: str):
    """Yield ``(filepath, rel_path, department)`` for every supported file under
    ``root``, recursing into department subfolders.

    ``rel_path`` is the path relative to ``root`` (e.g. ``hr/employment-basics.md``)
    and is used as the stable ``source`` id so files in different folders never
    collide. ``department`` is the top-level folder name, used as a fallback when
    a document does not declare its own department in frontmatter.
    """
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            extension = os.path.splitext(filename)[1].lower()
            if extension not in SUPPORTED_EXTENSIONS:
                continue
            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, root)
            parts = rel_path.split(os.sep)
            department = parts[0] if len(parts) > 1 else ""
            yield filepath, rel_path, department


def list_policy_documents() -> list[dict]:
    if not os.path.exists(DOCS_PATH):
        return []

    documents = []
    for filepath, rel_path, department in sorted(
        _iter_document_files(DOCS_PATH), key=lambda item: item[1]
    ):
        extension = os.path.splitext(filepath)[1].lower()
        documents.append({
            "filename": rel_path,
            "department": department,
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
        logger.warning("Created docs folder at %s. Add policy files there.", DOCS_PATH)
        return stats

    for filepath, rel_path, department_fallback in _iter_document_files(DOCS_PATH):
        extension = os.path.splitext(filepath)[1].lower()

        source_hash = file_hash(filepath)
        existing_chunks = collection.get(where={"source": rel_path})
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
            logger.info("Skipping %s (already indexed)", rel_path)
            stats["skipped"] += 1
            continue

        if existing_chunks.get("ids"):
            collection.delete(ids=existing_chunks["ids"])
            stats["deleted"] += len(existing_chunks["ids"])

        pages = load_document(filepath, department_fallback=department_fallback)
        chunk_docs = chunk_documents(pages)
        if not chunk_docs:
            logger.warning("Skipping %s (no extractable text)", rel_path)
            stats["skipped"] += 1
            continue

        # department/access_tier are uniform across a file; read them off the
        # loaded pages (chunking produces fresh Documents that don't carry them).
        department = pages[0].metadata.get("department", department_fallback)
        access_tier = pages[0].metadata.get("access_tier", DEFAULT_ACCESS_TIER)
        region = pages[0].metadata.get("region", "global")
        doc_type = pages[0].metadata.get("doc_type", "policy")
        version = pages[0].metadata.get("version", "2026.1")
        effective_date = pages[0].metadata.get("effective_date", "2026-01-01")
        status = pages[0].metadata.get("status", DEFAULT_STATUS)

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", rel_path)
        chunk_ids = [f"{safe_name}:{source_hash[:12]}:{i}" for i in range(len(chunk_docs))]

        documents = [doc.page_content for doc in chunk_docs]
        metadatas = [
            {
                "source": rel_path,
                "source_hash": source_hash,
                "chunk": i + 1,
                "chunk_version": CHUNK_VERSION,
                "type": extension.lstrip("."),
                "content_type": doc.metadata.get("content_type", ""),
                "parent_section": doc.metadata.get("parent_section", ""),
                "department": department,
                "access_tier": access_tier,
                "region": region,
                "doc_type": doc_type,
                "version": version,
                "effective_date": effective_date,
                "status": status,
            }
            for i, doc in enumerate(chunk_docs)
        ]

        get_vector_store().add_texts(texts=documents, ids=chunk_ids, metadatas=metadatas)
        stats["indexed"] += len(documents)
        logger.info(
            "Indexed %s (%d chunks, department=%s, access_tier=%s)",
            rel_path, len(documents), department, access_tier,
        )

    if stats["indexed"] > 0 or stats["deleted"] > 0:
        invalidate_bm25()

    return stats
