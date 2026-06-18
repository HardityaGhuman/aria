import hashlib
import os
import re
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rank_bm25 import BM25Okapi
from backend.core.config import (
    CHROMA_DB_PATH,
    DOCS_PATH,
    EMBEDDING_MODEL_NAME,
    EMBEDDINGS_LOCAL_ONLY,
    RETRIEVAL_TOP_K,
    RRF_K_CONSTANT,
    BM25_CANDIDATE_POOL,
    EXPAND_SECTION_RETRIEVAL,
)   

_embedding_fn = None
_vector_store = None
_bm25_corpus = None
_bm25_index = None
_bm25_metadata = None

SUPPORTED_EXTENSIONS = {".pdf"}
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
CHUNK_VERSION = "2026-05-08-pdf-rag-v3"
MAX_DISTANCE = 0.8
# This splits the text into chunks of size 4000 and overlap of 200
# Provided as a LangChain component so that we don't have to manually split the text
# This is a common technique in RAG to ensure that related information is kept together 
# in the same chunk
SECTION_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=4000,
    chunk_overlap=200,
    add_start_index=True,
)


@dataclass
class RetrievedContext:
    text: str
    sources: list[dict]

def get_embedding_function():
    global _embedding_fn
    if _embedding_fn is None:
        print("Loading embedding model (this may take a moment)...")
        _embedding_fn = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"local_files_only": EMBEDDINGS_LOCAL_ONLY},
        )
        print("Embedding model loaded.")
    return _embedding_fn


def get_vector_store():
    # This sets up the vector store Chroma component 
    # Chroma is a vector database that stores and retrieves text chunks based on their semantic similarity.
    # In simpler terms, it's like a smart library that understands the meaning of text, not just keywords.
    # It uses embeddings to represent text as vectors (numbers) and then finds the most similar vectors to a query.
    # This allows for more flexible and accurate retrieval of relevant information.
    global _vector_store
    if _vector_store is None:
        _vector_store = Chroma(
            collection_name="company_docs",
            embedding_function=get_embedding_function(),
            persist_directory=CHROMA_DB_PATH,
        )
    return _vector_store


def get_collection():
    """Return the underlying Chroma collection for existing hybrid retrieval code."""
    return get_vector_store()._collection


BM25_STOPWORDS = {
    "can", "i", "be", "for", "the", "a", "an", "is", "are", "was", "were",
    "to", "of", "in", "on", "at", "by", "or", "and", "my", "do", "did",
    "what", "how", "will", "it", "that", "this", "if", "not", "any", "me",
    "we", "you", "he", "she", "they", "them", "their", "its", "our", "has",
    "have", "had", "been", "with", "from", "about", "which", "when", "who"
}

def tokenize_for_bm25(text: str) -> list[str]:
    tokens = re.findall(r'\w+', text.lower())
    return [t for t in tokens if t not in BM25_STOPWORDS]

def get_bm25_index():
    global _bm25_corpus, _bm25_index, _bm25_metadata
    if _bm25_index is None:
        collection = get_collection()
        docs = collection.get(where={"content_type": {"$ne": "toc"}}, include=["documents", "metadatas"])
        _bm25_metadata = docs.get("metadatas", [])
        _bm25_corpus = docs.get("documents", [])
        
        tokenized_corpus = [tokenize_for_bm25(doc) for doc in _bm25_corpus] if _bm25_corpus else []
        _bm25_index = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
        
    return _bm25_index, _bm25_corpus, _bm25_metadata


def invalidate_bm25():
    global _bm25_corpus, _bm25_index, _bm25_metadata
    _bm25_corpus = None
    _bm25_index = None
    _bm25_metadata = None


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


def _load_pdf_file(filepath: str) -> list[Document]:
    from pypdf import PdfReader
    # Uses pypdf library to extract text from pdf
    reader = PdfReader(filepath)
    pages = []
    filename = os.path.basename(filepath)
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            # Document object created with page content and metadata
            pages.append(
                Document(
                    page_content=f"Page {page_number}\n{page_text}",
                    metadata={"page": page_number, "source": filename},
                )
            )
    return pages


def _load_document(filepath: str) -> list[Document]:
    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        return _load_pdf_file(filepath)
    return []


def _chunk_text(pages: list[Document]) -> list[Document]:
    if not pages:
        return []

    toc_pages = [p.page_content for p in pages if p.metadata.get("page") in (3, 4)]
    content_pages = [p.page_content for p in pages if p.metadata.get("page") not in (3, 4)]

    chunks = []

    if toc_pages:
        toc_full = _clean_text("\n\n".join(toc_pages))
        chunks.append(
            Document(
                page_content=toc_full,
                metadata={"content_type": "toc", "parent_section": ""},
            )
        )

    # Following section implements Structure-Aware chunking:
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


def _add_section_chunks(section: str, chunks: list[Document], content_type: str, parent_section: str = ""):
    max_chunk_size = 4000
    metadata = {"content_type": content_type, "parent_section": parent_section}
    if len(section) <= max_chunk_size:
        chunks.append(Document(page_content=section, metadata=metadata))
        return

    split_docs = SECTION_SPLITTER.split_documents(
        [Document(page_content=section, metadata=metadata)]
    )
    chunks.extend(doc for doc in split_docs if doc.page_content.strip())


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
        chunk_docs = _chunk_text(pages)
        if not chunk_docs:
            print(f"  Skipping {filename} (no extractable text)")
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
                **(
                    {"start_index": doc.metadata["start_index"]}
                    if "start_index" in doc.metadata
                    else {}
                ),
            }
            for i, doc in enumerate(chunk_docs)
        ]

        get_vector_store().add_texts(texts=documents, ids=chunk_ids, metadatas=metadatas)
        stats["indexed"] += len(documents)
        print(f"  Indexed {filename} ({len(documents)} chunks)")

    if stats["indexed"] > 0 or stats["deleted"] > 0:
        invalidate_bm25()

    return stats


def retrieve_context(query: str, n_results: int = None) -> RetrievedContext:
    """
    Query ChromaDB using hybrid BM25 + Vector search with Reciprocal Rank Fusion (RRF).
    """
    if n_results is None:
        n_results = RETRIEVAL_TOP_K

    collection = get_collection()
    if collection.count() == 0:
        return RetrievedContext(
            text="No company policy documents have been indexed yet.",
            sources=[],
        )

    # 1. Vector Search
    vec_results = collection.query(
        query_texts=[query],
        n_results=BM25_CANDIDATE_POOL,
        where={"content_type": {"$ne": "toc"}},
        include=["documents", "metadatas", "distances"],
    )

    vec_chunks = vec_results["documents"][0] if vec_results.get("documents") and vec_results["documents"] else []
    vec_metadatas = vec_results["metadatas"][0] if vec_results.get("metadatas") and vec_results["metadatas"] else []
    vec_distances = vec_results.get("distances", [[]])[0] if vec_results.get("distances") and vec_results["distances"] else []

    vec_valid = []
    chunk_dict = {}
    for chunk, meta, dist in zip(vec_chunks, vec_metadatas, vec_distances):
        if dist is not None and dist <= MAX_DISTANCE:
            chunk_id = f"{meta.get('source')}:{meta.get('chunk')}"
            vec_valid.append(chunk_id)
            chunk_dict[chunk_id] = {"chunk": chunk, "metadata": meta, "distance": dist}

    # 2. BM25 Search
    bm25_index, bm25_corpus, bm25_metadata = get_bm25_index()
    bm25_valid = []
    if bm25_index is not None:
        tokenized_query = tokenize_for_bm25(query)
        bm25_scores = bm25_index.get_scores(tokenized_query)
        scored_indices = sorted(enumerate(bm25_scores), key=lambda x: x[1], reverse=True)[:BM25_CANDIDATE_POOL]
        
        for idx, score in scored_indices:
            if score > 0:
                meta = bm25_metadata[idx]
                chunk = bm25_corpus[idx]
                chunk_id = f"{meta.get('source')}:{meta.get('chunk')}"
                bm25_valid.append(chunk_id)
                if chunk_id not in chunk_dict:
                    chunk_dict[chunk_id] = {"chunk": chunk, "metadata": meta, "distance": None}

    # 3. RRF Merging
    all_candidates = set(vec_valid) | set(bm25_valid)
    if not all_candidates:
        return RetrievedContext(text="No relevant context found.", sources=[])

    rrf_scores = {}
    fallback_rank = BM25_CANDIDATE_POOL + 1
    for chunk_id in all_candidates:
        vec_rank = vec_valid.index(chunk_id) + 1 if chunk_id in vec_valid else fallback_rank
        bm25_rank = bm25_valid.index(chunk_id) + 1 if chunk_id in bm25_valid else fallback_rank
        
        score = (1 / (RRF_K_CONSTANT + vec_rank)) + (1 / (RRF_K_CONSTANT + bm25_rank))
        rrf_scores[chunk_id] = score

    # Sort descending by RRF score and pick top K
    sorted_chunks = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)[:n_results]

    # Build initial context map
    context_map = {}
    for chunk_id in sorted_chunks:
        context_map[chunk_id] = chunk_dict[chunk_id]

    # Parent section expansion
    if EXPAND_SECTION_RETRIEVAL:
        parent_sections = set(context_map[cid]["metadata"].get("parent_section") for cid in context_map if context_map[cid]["metadata"].get("parent_section"))
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
