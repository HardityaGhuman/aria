"""Offline, one-time document indexer.

Run this once (and again whenever the policy PDFs or the chunking logic change)
to (re)build the pgvector authoritative store from the files in
``backend/data/docs``.

Indexing is deliberately kept out of the running web app: the API server only
reads the prebuilt index, it never ingests documents at request time.

Usage:
    ./venv/bin/python -m backend.index_documents
    # or
    ./venv/bin/python backend/index_documents.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.rag import initialize_vectorstore
from backend.rag.vector_schema import ensure_vector_extension, initialize_vector_store_schema


def main() -> int:
    # Bootstrap the store so the indexer can run standalone, before the server's
    # first boot (create the pgvector extension + tables if absent; idempotent).
    ensure_vector_extension()
    initialize_vector_store_schema()
    print("Building vector store from policy documents...")
    stats = initialize_vectorstore()
    print(
        "Done. "
        f"{stats.get('indexed', 0)} chunk(s) indexed, "
        f"{stats.get('skipped', 0)} file(s) unchanged, "
        f"{stats.get('deleted', 0)} stale chunk(s) removed."
    )
    if stats.get("indexed", 0) == 0 and stats.get("skipped", 0) == 0:
        print(
            "No documents were indexed. Add PDF policy files to "
            "backend/data/docs and run this again."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
