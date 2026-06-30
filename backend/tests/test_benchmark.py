"""Aggregation logic for the benchmark — tested with a fake strategy so it does
not depend on a built index (that's the live baseline run, not a unit test)."""
from backend.eval import benchmark


def test_aggregate_groups_by_difficulty_and_query_type():
    rows = [
        {"difficulty": "easy", "query_type": "single_doc", "doc_recall": 1.0,
         "doc_precision": 0.5, "doc_hit": 1.0, "doc_mrr": 1.0},
        {"difficulty": "hard", "query_type": "vocab_gap", "doc_recall": 0.0,
         "doc_precision": 0.0, "doc_hit": 0.0, "doc_mrr": 0.0},
    ]
    agg = benchmark.aggregate(rows)
    assert agg["overall"]["doc_recall"] == 0.5
    assert agg["by_difficulty"]["easy"]["doc_recall"] == 1.0
    assert agg["by_difficulty"]["hard"]["doc_hit"] == 0.0
    assert agg["by_query_type"]["vocab_gap"]["doc_precision"] == 0.0


def test_evaluate_retrieval_uses_source_metadata(monkeypatch):
    # Fake strategy returns objects with .metadata["source"], like Candidate.
    class C:
        def __init__(self, src):
            self.metadata = {"source": src}

    fake = {"hybrid": lambda q, *a, **k: [C("time-and-leave/working-hours-and-pto.md"), C("hr/employment-basics.md")]}
    monkeypatch.setattr(benchmark, "STRATEGIES", fake)

    item = {
        "id": "q1", "question": "pto?", "difficulty": "easy", "query_type": "single_doc",
        "department": "time-and-leave",
        "expected_document_ids": ["time-and-leave/working-hours-and-pto.md"],
    }
    rows = benchmark.evaluate_retrieval(dataset=[item], strategy="hybrid", k=2)
    assert rows[0]["doc_hit"] == 1.0
    assert rows[0]["doc_recall"] == 1.0
    assert rows[0]["doc_precision"] == 0.5
