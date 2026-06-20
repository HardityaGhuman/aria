"""
rag/schema.py
-------------
Shared data structures returned by the retrieval layer.
"""
from dataclasses import dataclass


@dataclass
class RetrievedContext:
    """The formatted context block plus structured per-chunk source metadata."""

    text: str
    sources: list[dict]
