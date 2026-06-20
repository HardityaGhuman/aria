"""
rag/embedding.py
----------------
Dense embedding model. Loaded lazily and cached as a module-level singleton so
the (slow) model load happens once per process.
"""
# pyrefly: ignore [missing-import]
from langchain_huggingface import HuggingFaceEmbeddings

from backend.core.config import EMBEDDING_MODEL_NAME, EMBEDDINGS_LOCAL_ONLY
from backend.core.logging import get_logger

logger = get_logger(__name__)

_embedding_fn = None


def get_embedding_function():
    global _embedding_fn
    if _embedding_fn is None:
        logger.info("Loading embedding model '%s' (this may take a moment)...", EMBEDDING_MODEL_NAME)
        _embedding_fn = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"local_files_only": EMBEDDINGS_LOCAL_ONLY},
        )
        logger.info("Embedding model loaded.")
    return _embedding_fn
