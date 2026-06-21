"""
eval/metrics.py
---------------
Reference-based evaluation metrics. Unlike the live LLM-as-judge (reference-free,
lenient), these compare the pipeline's output against known-correct ground truth,
so they can detect a retrieval miss.

Retrieval metrics operate on the ordered list of section names the retriever
returned (the ``parent_section`` of each top-k chunk) versus the expected
sections. Answer metrics compare the generated answer against the ground truth.
"""
import re


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _section_matches(retrieved: str, expected: str) -> bool:
    """Lenient section match: containment either way so "PTO" matches
    "Paid time off (PTO)"."""
    r, e = _norm(retrieved), _norm(expected)
    if not r or not e:
        return False
    return e in r or r in e


def _is_relevant(retrieved_section: str, expected_sections: list[str]) -> bool:
    return any(_section_matches(retrieved_section, e) for e in expected_sections)


# ── Retrieval metrics ────────────────────────────────────────────────
def section_recall(retrieved_sections: list[str], expected_sections: list[str]) -> float:
    """Fraction of expected sections that were retrieved (0–1)."""
    if not expected_sections:
        return 1.0
    hits = sum(
        1 for e in expected_sections
        if any(_section_matches(r, e) for r in retrieved_sections)
    )
    return hits / len(expected_sections)


def hit_at_k(retrieved_sections: list[str], expected_sections: list[str]) -> float:
    """1.0 if any expected section is in the top-k, else 0.0."""
    return 1.0 if any(_is_relevant(r, expected_sections) for r in retrieved_sections) else 0.0


def context_hit_rate(retrieved_sections: list[str], expected_sections: list[str]) -> float:
    """Fraction of retrieved chunks that are from an expected section (focus)."""
    if not retrieved_sections:
        return 0.0
    relevant = sum(1 for r in retrieved_sections if _is_relevant(r, expected_sections))
    return relevant / len(retrieved_sections)


def reciprocal_rank(retrieved_sections: list[str], expected_sections: list[str]) -> float:
    """1/rank of the first relevant chunk (Mean Reciprocal Rank component)."""
    for rank, section in enumerate(retrieved_sections, start=1):
        if _is_relevant(section, expected_sections):
            return 1.0 / rank
    return 0.0


# ── Answer metric ────────────────────────────────────────────────────
def answer_coverage(answer: str, ground_truth: str, min_term_length: int = 4) -> float:
    """Fraction of significant ground-truth terms present in the answer.

    A crude keyword-overlap sanity check — not a substitute for RAGAS, but a
    cheap, deterministic signal of whether the key facts made it into the answer.
    """
    if not answer or not ground_truth:
        return 0.0
    answer_norm = _norm(answer)
    terms = [t for t in _norm(ground_truth).split() if len(t) >= min_term_length]
    if not terms:
        return 1.0
    hits = sum(1 for t in terms if t in answer_norm)
    return hits / len(terms)
