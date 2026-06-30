"""Unit tests for document-level retrieval metrics (eval Layer 1).

Matching is EXACT source equality — no substring fuzz — so a chunk from
'hr/code-of-conduct.md' never counts as a hit for 'hr/code-of-conduct-2.md'.
"""
from backend.eval import metrics


# retrieved_sources is best-first, with duplicates (chunks share a source).
EXPECTED = ["time-and-leave/working-hours-and-pto.md"]


def test_recall_perfect_when_expected_present():
    retrieved = ["time-and-leave/working-hours-and-pto.md", "hr/employment-basics.md"]
    assert metrics.doc_recall_at_k(retrieved, EXPECTED) == 1.0


def test_recall_zero_when_absent():
    assert metrics.doc_recall_at_k(["hr/employment-basics.md"], EXPECTED) == 0.0


def test_recall_partial_for_multi_doc_expected():
    expected = ["finance/compensation-and-payroll.md", "finance/salary-bands.csv"]
    retrieved = ["finance/compensation-and-payroll.md", "hr/employment-basics.md"]
    assert metrics.doc_recall_at_k(retrieved, expected) == 0.5


def test_recall_empty_expected_is_one():
    assert metrics.doc_recall_at_k(["x"], []) == 1.0


def test_precision_counts_relevant_chunks():
    # 2 of 4 retrieved chunks are from the expected doc.
    retrieved = [
        "time-and-leave/working-hours-and-pto.md",
        "time-and-leave/working-hours-and-pto.md",
        "hr/employment-basics.md",
        "it/equipment-and-devices.md",
    ]
    assert metrics.doc_precision_at_k(retrieved, EXPECTED) == 0.5


def test_precision_zero_for_empty_retrieved():
    assert metrics.doc_precision_at_k([], EXPECTED) == 0.0


def test_hit_is_binary():
    assert metrics.doc_hit_at_k(["hr/employment-basics.md", "time-and-leave/working-hours-and-pto.md"], EXPECTED) == 1.0
    assert metrics.doc_hit_at_k(["hr/employment-basics.md"], EXPECTED) == 0.0


def test_mrr_uses_first_relevant_rank():
    # First expected chunk is at rank 2 -> 0.5.
    retrieved = ["hr/employment-basics.md", "time-and-leave/working-hours-and-pto.md"]
    assert metrics.doc_reciprocal_rank(retrieved, EXPECTED) == 0.5
    assert metrics.doc_reciprocal_rank(["hr/employment-basics.md"], EXPECTED) == 0.0


def test_exact_match_no_substring_fuzz():
    # A lexically similar but different path must NOT count.
    retrieved = ["time-and-leave/working-hours-and-pto-2025.md"]
    assert metrics.doc_hit_at_k(retrieved, EXPECTED) == 0.0


def test_answer_coverage_keyword_overlap():
    assert metrics.answer_coverage("20 days of paid time off", "employees get 20 days paid time off") > 0.5
    assert metrics.answer_coverage("", "anything") == 0.0
