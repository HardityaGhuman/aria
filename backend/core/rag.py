import os
import chromadb
from chromadb.utils import embedding_functions
from backend.core.config import CHROMA_DB_PATH, DOCS_PATH

# Use Chroma's built-in sentence-transformers embedding (runs locally, free)
EMBEDDING_FN = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

_client = None
_collection = None


def get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _collection = _client.get_or_create_collection(
            name="company_docs",
            embedding_function=EMBEDDING_FN
        )
    return _collection


def initialize_vectorstore():
    """
    Load all .txt files from DOCS_PATH into ChromaDB.
    Skips documents already indexed (idempotent).
    """
    collection = get_collection()

    if not os.path.exists(DOCS_PATH):
        print(f"Docs folder not found at {DOCS_PATH}. Skipping ingestion.")
        return

    for filename in os.listdir(DOCS_PATH):
        if not filename.endswith(".txt"):
            continue

        doc_id = filename  # Use filename as the unique ID

        # Check if document already exists
        existing = collection.get(ids=[doc_id])
        # Chromadb .get() returns a dict with 'ids', 'documents', etc.
        # If the ID exists, it will be in the 'ids' list.
        # However, we are chunking, so the IDs in the DB are actually chunk IDs (doc_id_chunk0, ...).
        # We need a robust way to check.
        # A simple way based on the previous code's intent:
        # The previous code tried `existing["ids"]`, but `collection.get(ids=[doc_id])` would look for exact match of `doc_id`.
        # But we save chunks as `doc_id_chunkX`.
        # So we should query by metadata or just check if ANY chunk with this source exists.
        
        existing_chunks = collection.get(where={"source": filename})
        
        if existing_chunks and existing_chunks["ids"]:
            print(f"  Skipping {filename} (already indexed)")
            continue

        filepath = os.path.join(DOCS_PATH, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        # Simple chunking: split on double newlines
        chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
        if not chunks:
             print(f"  Skipping {filename} (empty or only whitespace)")
             continue
             
        chunk_ids = [f"{doc_id}_chunk{i}" for i in range(len(chunks))]
        metadatas = [{"source": filename} for _ in chunks]

        collection.add(documents=chunks, ids=chunk_ids, metadatas=metadatas)
        print(f"  Indexed {filename} ({len(chunks)} chunks)")


def retrieve_context(query: str, n_results: int = 3) -> str:
    """
    Query ChromaDB and return the top matching document chunks as a string.
    """
    collection = get_collection()
    results = collection.query(query_texts=[query], n_results=n_results)

    if not results["documents"] or not results["documents"][0]:
        return "No relevant context found."

    chunks = results["documents"][0]
    sources = [m["source"] for m in results["metadatas"][0]]

    formatted = []
    for chunk, source in zip(chunks, sources):
        formatted.append(f"[Source: {source}]\n{chunk}")

    return "\n\n".join(formatted)