"""
rag/schema.py
-------------
Shared data structures returned by the retrieval layer.
"""
from dataclasses import dataclass, field


@dataclass
class Candidate:
    """A single retrieved chunk with the signals a strategy produced for it."""

    chunk_id: str
    text: str
    metadata: dict
    score: float | None = None      # fused / BM25 relevance score
    distance: float | None = None   # vector cosine distance (lower = closer)


@dataclass
class RetrievedContext:
    """The formatted context block plus structured per-chunk source metadata."""

    text: str
    sources: list[dict] = field(default_factory=list)
    status: str = "ok"            # ok | empty | blocked
    blocked_contact: str | None = None  # who to contact when status == "blocked"
