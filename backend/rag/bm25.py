"""
rag/bm25.py
-----------
Lexical (keyword) retrieval index built over the Chroma corpus with BM25.

The index is held in memory and rebuilt lazily on first use; call
``invalidate_bm25()`` after (re)indexing so it is rebuilt from the fresh corpus.
"""
import re

from rank_bm25 import BM25Okapi

from backend.rag.vector_store import get_collection

_bm25_corpus = None
_bm25_index = None
_bm25_metadata = None

BM25_STOPWORDS = {
    "can", "i", "be", "for", "the", "a", "an", "is", "are", "was", "were",
    "to", "of", "in", "on", "at", "by", "or", "and", "my", "do", "did",
    "what", "how", "will", "it", "that", "this", "if", "not", "any", "me",
    "we", "you", "he", "she", "they", "them", "their", "its", "our", "has",
    "have", "had", "been", "with", "from", "about", "which", "when", "who"
}


def _stem(token: str) -> str:
    """Conservative plural/inflection normaliser so lexical search matches across
    number (e.g. "leaves"->"leave", "holidays"->"holiday", "policies"->"policy").

    Document-agnostic and deliberately minimal: it only strips common English
    plural endings and guards against false roots like "process"->"proces".
    BM25 has no built-in stemming, so without this a query for "types of leaves"
    scores zero against documents that say "leave".
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if (
        len(token) > 3
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


def tokenize_for_bm25(text: str) -> list[str]:
    tokens = re.findall(r"\w+", text.lower())
    return [_stem(t) for t in tokens if t not in BM25_STOPWORDS]


def get_bm25_index():
    global _bm25_corpus, _bm25_index, _bm25_metadata
    if _bm25_index is None:
        collection = get_collection()
        docs = collection.get(
            where={"content_type": {"$ne": "toc"}},
            include=["documents", "metadatas"],
        )
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
