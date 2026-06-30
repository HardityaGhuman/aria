"""
eval/metrics.py
---------------
Document-level retrieval metrics for the offline harness. They compare the
pipeline's retrieved documents against known-correct ground truth, so they
detect a retrieval miss (unlike a reference-free LLM judge).

The match unit is the document id = a chunk's ``metadata["source"]`` (a path
relative to the docs root, e.g. ``time-and-leave/working-hours-and-pto.md``).
Matching is EXACT equality — never substring — so lenient matching can't
inflate the score.

``retrieved_sources`` is the ordered (best-first) list of each top-k chunk's
source, WITH duplicates (multiple chunks often share a document).
"""
import re


# ── Retrieval metrics (document-level) ───────────────────────────────
def doc_recall_at_k(retrieved_sources: list[str], expected_docs: list[str]) -> float:
    """Fraction of expected documents that appear anywhere in the top-k (0–1)."""
    if not expected_docs:
        return 1.0
    retrieved_set = set(retrieved_sources)
    found = sum(1 for d in expected_docs if d in retrieved_set)
    return found / len(expected_docs)


def doc_precision_at_k(retrieved_sources: list[str], expected_docs: list[str]) -> float:
    """Fraction of retrieved chunks that come from an expected document.

    This is the 'noise' metric the document viewer cares about: how much of what
    surfaced is actually relevant. Operates per chunk (with duplicates), so a
    top-k full of one wrong document scores low."""
    if not retrieved_sources:
        return 0.0
    expected_set = set(expected_docs)
    relevant = sum(1 for s in retrieved_sources if s in expected_set)
    return relevant / len(retrieved_sources)


def doc_hit_at_k(retrieved_sources: list[str], expected_docs: list[str]) -> float:
    """1.0 if any expected document is in the top-k, else 0.0."""
    expected_set = set(expected_docs)
    return 1.0 if any(s in expected_set for s in retrieved_sources) else 0.0


def doc_reciprocal_rank(retrieved_sources: list[str], expected_docs: list[str]) -> float:
    """1/rank of the first chunk from an expected document (MRR component)."""
    expected_set = set(expected_docs)
    for rank, source in enumerate(retrieved_sources, start=1):
        if source in expected_set:
            return 1.0 / rank
    return 0.0


# ── Answer metric (cheap, deterministic sanity check) ────────────────
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def answer_coverage(answer: str, ground_truth: str, min_term_length: int = 4) -> float:
    """Fraction of significant ground-truth terms present in the answer.

    A crude keyword-overlap signal — NOT a substitute for RAGAS, but a cheap,
    deterministic pre-check of whether the key facts made it into the answer."""
    if not answer or not ground_truth:
        return 0.0
    answer_norm = _norm(answer)
    terms = [t for t in _norm(ground_truth).split() if len(t) >= min_term_length]
    if not terms:
        return 1.0
    hits = sum(1 for t in terms if t in answer_norm)
    return hits / len(terms)
