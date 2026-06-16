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
    RETRIEVAL_TOP_K,
    EXPAND_SECTION_RETRIEVAL,
)

_embedding_fn = None
_client = None
_collection = None

SUPPORTED_EXTENSIONS = {".pdf"}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
CHUNK_VERSION = "2026-05-08-pdf-rag-v3"
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


def _load_pdf_file(filepath: str) -> list[dict]:
    from pypdf import PdfReader

    reader = PdfReader(filepath)
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append({"page": page_number, "text": f"Page {page_number}\n{page_text}"})
    return pages


def _load_document(filepath: str) -> list[dict]:
    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        return _load_pdf_file(filepath)
    return []


def _chunk_text(pages: list[dict]) -> list[dict]:
    if not pages:
        return []

    toc_pages = [p["text"] for p in pages if p["page"] in (3, 4)]
    content_pages = [p["text"] for p in pages if p["page"] not in (3, 4)]

    chunks = []

    if toc_pages:
        toc_full = _clean_text("\n\n".join(toc_pages))
        chunks.append({"text": toc_full, "content_type": "toc", "parent_section": ""})

    if content_pages:
        content_full = _clean_text("\n\n".join(content_pages))
        boundary_pattern = re.compile(r'\n\s*(?=(?:[IVX]+\.|[IVX]+\.[A-Z]\.|[A-Z]\.)\s+[A-Z])')
        
        sections = []
        last_idx = 0
        for match in boundary_pattern.finditer(content_full):
            idx = match.start()
            if idx > last_idx:
                section = content_full[last_idx:idx].strip()
                if section:
                    sections.append(section)
            last_idx = idx
            
        if last_idx < len(content_full):
            section = content_full[last_idx:].strip()
            if section:
                sections.append(section)

        merged_sections = []
        buffer = ""
        for section in sections:
            if buffer:
                section = buffer + "\n\n" + section
                buffer = ""
                
            lines = [line for line in section.split('\n') if line.strip()]
            if len(lines) <= 2 and len(section) < 200:
                buffer = section
            else:
                merged_sections.append(section)
                
        if buffer:
            if merged_sections:
                merged_sections[-1] += "\n\n" + buffer
            else:
                merged_sections.append(buffer)

        current_parent_section = ""
        for section in merged_sections:
            match = re.search(r'^([IVXLCDM]+)\.', section.strip())
            if match:
                current_parent_section = match.group(1)
            
            _add_section_chunks(section, chunks, "content", current_parent_section)

    return chunks


def _add_section_chunks(section: str, chunks: list[dict], content_type: str, parent_section: str = ""):
    max_chunk_size = 4000
    if len(section) <= max_chunk_size:
        chunks.append({"text": section, "content_type": content_type, "parent_section": parent_section})
        return
        
    start = 0
    while start < len(section):
        end = min(start + max_chunk_size, len(section))
        if end < len(section):
            paragraph_break = section.rfind("\n\n", start, end)
            sentence_break = section.rfind(". ", start, end)
            split_at = max(paragraph_break, sentence_break)
            if split_at > start + max_chunk_size // 2:
                end = split_at + 1
        
        chunk = section[start:end].strip()
        if chunk:
            chunks.append({"text": chunk, "content_type": content_type, "parent_section": parent_section})
            
        if end == len(section):
            start = end
        else:
            start = max(end - 200, start + 1)
            next_break = section.find("\n", start, min(start + 120, len(section)))
            if next_break != -1:
                start = next_break + 1


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

        pages = _load_document(filepath)
        chunk_dicts = _chunk_text(pages)
        if not chunk_dicts:
            print(f"  Skipping {filename} (no extractable text)")
            stats["skipped"] += 1
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
        chunk_ids = [f"{safe_name}:{source_hash[:12]}:{i}" for i in range(len(chunk_dicts))]
        
        documents = [cd["text"] for cd in chunk_dicts]
        metadatas = [
            {
                "source": filename,
                "source_hash": source_hash,
                "chunk": i + 1,
                "chunk_version": CHUNK_VERSION,
                "type": extension.lstrip("."),
                "content_type": cd["content_type"],
                "parent_section": cd.get("parent_section", ""),
            }
            for i, cd in enumerate(chunk_dicts)
        ]

        collection.add(documents=documents, ids=chunk_ids, metadatas=metadatas)
        stats["indexed"] += len(documents)
        print(f"  Indexed {filename} ({len(documents)} chunks)")

    return stats


def retrieve_context(query: str, n_results: int = None) -> RetrievedContext:
    """
    Query ChromaDB and return relevant policy chunks with source metadata.
    """
    if n_results is None:
        n_results = RETRIEVAL_TOP_K

    collection = get_collection()
    if collection.count() == 0:
        return RetrievedContext(
            text="No company policy documents have been indexed yet.",
            sources=[],
        )

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        where={"content_type": {"$ne": "toc"}},
        include=["documents", "metadatas", "distances"],
    )

    if not results["documents"] or not results["documents"][0]:
        return RetrievedContext(text="No relevant context found.", sources=[])

    chunks = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results.get("distances", [[]])[0]

    valid_indices = [i for i, d in enumerate(distances) if d is not None and d <= MAX_DISTANCE]
    if not valid_indices:
        return RetrievedContext(text="No relevant context found.", sources=[])

    # Build initial context map
    context_map = {}
    for i in valid_indices:
        metadata = metadatas[i]
        chunk_id = f"{metadata.get('source')}:{metadata.get('chunk')}"
        context_map[chunk_id] = {
            "chunk": chunks[i],
            "metadata": metadata,
            "distance": distances[i]
        }

    # Parent section expansion
    if EXPAND_SECTION_RETRIEVAL:
        parent_sections = set(metadatas[i].get("parent_section") for i in valid_indices if metadatas[i].get("parent_section"))
        if parent_sections:
            expanded_results = collection.get(
                where={
                    "$and": [
                        {"content_type": {"$ne": "toc"}},
                        {"parent_section": {"$in": list(parent_sections)}}
                    ]
                },
                include=["documents", "metadatas"]
            )
            if expanded_results and expanded_results["documents"]:
                for exp_chunk, exp_metadata in zip(expanded_results["documents"], expanded_results["metadatas"]):
                    chunk_id = f"{exp_metadata.get('source')}:{exp_metadata.get('chunk')}"
                    if chunk_id not in context_map:
                        context_map[chunk_id] = {
                            "chunk": exp_chunk,
                            "metadata": exp_metadata,
                            "distance": None  # Expansions don't have direct distance scores
                        }

    # Ensure consistent ordering
    sorted_items = sorted(context_map.values(), key=lambda x: (x["metadata"].get("source", ""), x["metadata"].get("chunk", 0)))

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
