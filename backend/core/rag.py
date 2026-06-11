import hashlib
import os
import re
from dataclasses import dataclass

import chromadb
from chromadb.utils import embedding_functions
from backend.core.config import (
    CHROMA_DB_PATH,
    DOCS_PATH,
    EMBEDDING_MODEL_NAME,
    EMBEDDINGS_LOCAL_ONLY,
)

_embedding_fn = None
_client = None
_collection = None

SUPPORTED_EXTENSIONS = {".pdf"}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
CHUNK_VERSION = "2026-05-08-pdf-rag-v1"
MAX_DISTANCE = 0.62


@dataclass
class RetrievedContext:
    text: str
    sources: list[dict]

def get_embedding_function():
    global _embedding_fn
    if _embedding_fn is None:
        print("Loading embedding model (this may take a moment)...")
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBEDDING_MODEL_NAME,
            local_files_only=EMBEDDINGS_LOCAL_ONLY,
        )
        print("Embedding model loaded.")
    return _embedding_fn

def get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _collection = _client.get_or_create_collection(
            name="company_docs",
            embedding_function=get_embedding_function()
        )
    return _collection


def _file_hash(filepath: str) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_pdf_file(filepath: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(filepath)
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(f"Page {page_number}\n{page_text}")
    return "\n\n".join(pages)


def _load_document(filepath: str) -> str:
    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        return _load_pdf_file(filepath)
    return ""


def _chunk_text(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        if end < len(text):
            paragraph_break = text.rfind("\n\n", start, end)
            sentence_break = text.rfind(". ", start, end)
            split_at = max(paragraph_break, sentence_break)
            if split_at > start + CHUNK_SIZE // 2:
                end = split_at + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            start = end
        else:
            start = max(end - CHUNK_OVERLAP, start + 1)
            next_break = text.find("\n", start, min(start + 120, len(text)))
            if next_break != -1:
                start = next_break + 1
            while start < len(text) and text[start].isalnum() and text[start - 1].isalnum():
                start += 1
    return chunks


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
    """
    Ingest PDF policy documents into ChromaDB.
    Changed files are reindexed based on a content hash.
    """
    collection = get_collection()
    stats = {"indexed": 0, "skipped": 0, "deleted": 0}

    old_text_chunks = collection.get(where={"type": "txt"})
    if old_text_chunks.get("ids"):
        collection.delete(ids=old_text_chunks["ids"])
        stats["deleted"] += len(old_text_chunks["ids"])
        print(f"  Removed {len(old_text_chunks['ids'])} old TXT chunk(s)")

    if not os.path.exists(DOCS_PATH):
        os.makedirs(DOCS_PATH, exist_ok=True)
        print(f"Created docs folder at {DOCS_PATH}. Add PDF policy files there.")
        return stats

    for filename in sorted(os.listdir(DOCS_PATH)):
        filepath = os.path.join(DOCS_PATH, filename)
        extension = os.path.splitext(filename)[1].lower()
        if not os.path.isfile(filepath) or extension not in SUPPORTED_EXTENSIONS:
            continue

        source_hash = _file_hash(filepath)
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
            print(f"  Skipping {filename} (already indexed)")
            stats["skipped"] += 1
            continue

        if existing_chunks.get("ids"):
            collection.delete(ids=existing_chunks["ids"])
            stats["deleted"] += len(existing_chunks["ids"])

        text = _load_document(filepath)
        chunks = _chunk_text(text)
        if not chunks:
            print(f"  Skipping {filename} (no extractable text)")
            stats["skipped"] += 1
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
        chunk_ids = [f"{safe_name}:{source_hash[:12]}:{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "source": filename,
                "source_hash": source_hash,
                "chunk": i + 1,
                "chunk_version": CHUNK_VERSION,
                "type": extension.lstrip("."),
            }
            for i in range(len(chunks))
        ]

        collection.add(documents=chunks, ids=chunk_ids, metadatas=metadatas)
        stats["indexed"] += len(chunks)
        print(f"  Indexed {filename} ({len(chunks)} chunks)")

    return stats


def retrieve_context(query: str, n_results: int = 6) -> RetrievedContext:
    """
    Query ChromaDB and return relevant policy chunks with source metadata.
    """
    collection = get_collection()
    if collection.count() == 0:
        return RetrievedContext(
            text="No company policy documents have been indexed yet.",
            sources=[],
        )

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return RetrievedContext(text="No relevant context found.", sources=[])

    chunks = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    formatted = []
    sources = []
    for chunk, metadata, distance in zip(chunks, metadatas, distances):
        if distance is not None and distance > MAX_DISTANCE:
            continue
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
