"""
eval/benchmark.py
-----------------
Batch document-level retrieval evaluation over the labeled dataset.

For each question it runs a retrieval strategy over the FULL corpus (no RBAC
filter — ranking quality in isolation), compares the retrieved documents
(each chunk's metadata['source']) against expected_document_ids, and aggregates:

    doc_recall@k     — fraction of expected docs retrieved
    doc_precision@k  — fraction of retrieved chunks from an expected doc (noise)
    doc_hit@k        — did any expected doc make the top-k
    doc_mrr          — reciprocal rank of the first relevant chunk

Aggregated overall, by difficulty, and by query_type so weak corners
(vocab_gap, tabular) are visible, not averaged away. No LLM calls.

Run:
    python -m backend.eval.benchmark            # hybrid
    python -m backend.eval.benchmark vector     # one strategy
    python -m backend.eval.benchmark compare    # all strategies -> results/
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from backend.core.config import RETRIEVAL_TOP_K
from backend.eval import metrics
from backend.eval.dataset import load_eval_dataset
from backend.rag.strategies import STRATEGIES

RESULTS_DIR = Path(__file__).with_name("results")
DIFFICULTY_ORDER = ["easy", "moderate", "hard"]
QUERY_TYPE_ORDER = ["single_doc", "cross_doc", "vocab_gap", "tabular"]
METRIC_KEYS = ["doc_recall", "doc_precision", "doc_hit", "doc_mrr"]


def _retrieved_sources(query: str, strategy: str, k: int) -> list[str]:
    """Top-k retrieved chunks' source ids, best-first (with duplicates)."""
    candidates = STRATEGIES[strategy](query)[:k]
    return [c.metadata.get("source", "") for c in candidates]


def evaluate_retrieval(dataset: list[dict] | None = None, strategy: str = "hybrid", k: int | None = None) -> list[dict]:
    """Per-question document-level retrieval scores for one strategy."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}'. Choose from {list(STRATEGIES)}.")
    dataset = dataset if dataset is not None else load_eval_dataset()
    k = k or RETRIEVAL_TOP_K

    rows = []
    for item in dataset:
        retrieved = _retrieved_sources(item["question"], strategy, k)
        expected = item["expected_document_ids"]
        rows.append({
            "id": item.get("id", ""),
            "question": item["question"],
            "difficulty": item.get("difficulty", "easy"),
            "query_type": item.get("query_type", "single_doc"),
            "department": item.get("department", ""),
            "expected": expected,
            "retrieved": retrieved,
            "doc_recall": metrics.doc_recall_at_k(retrieved, expected),
            "doc_precision": metrics.doc_precision_at_k(retrieved, expected),
            "doc_hit": metrics.doc_hit_at_k(retrieved, expected),
            "doc_mrr": metrics.doc_reciprocal_rank(retrieved, expected),
        })
    return rows


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _means(rows: list[dict]) -> dict:
    return {m: _mean([r[m] for r in rows]) for m in METRIC_KEYS}


def aggregate(rows: list[dict]) -> dict:
    """Overall + per-difficulty + per-query_type mean metrics."""
    by_difficulty = {
        lvl: _means([r for r in rows if r["difficulty"] == lvl])
        for lvl in DIFFICULTY_ORDER
        if any(r["difficulty"] == lvl for r in rows)
    }
    by_query_type = {
        qt: _means([r for r in rows if r["query_type"] == qt])
        for qt in QUERY_TYPE_ORDER
        if any(r["query_type"] == qt for r in rows)
    }
    return {"overall": _means(rows), "by_difficulty": by_difficulty, "by_query_type": by_query_type}


def _print_report(strategy: str, agg: dict) -> None:
    print(f"\n=== strategy={strategy}  k={RETRIEVAL_TOP_K} ===")
    o = agg["overall"]
    print(f"overall: recall={o['doc_recall']:.2f} precision={o['doc_precision']:.2f} "
          f"hit={o['doc_hit']:.2f} mrr={o['doc_mrr']:.2f}")
    for label, section in (("difficulty", agg["by_difficulty"]), ("query_type", agg["by_query_type"])):
        print(f"  by {label}:")
        for key, m in section.items():
            print(f"    {key:<12} recall={m['doc_recall']:.2f} precision={m['doc_precision']:.2f} "
                  f"hit={m['doc_hit']:.2f} mrr={m['doc_mrr']:.2f}")


def run(strategy: str = "hybrid") -> dict:
    rows = evaluate_retrieval(strategy=strategy)
    agg = aggregate(rows)
    _print_report(strategy, agg)
    return {"strategy": strategy, "aggregate": agg, "rows": rows}


def compare() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"), "k": RETRIEVAL_TOP_K, "strategies": {}}
    for strategy in STRATEGIES:
        result = run(strategy)
        out["strategies"][strategy] = result["aggregate"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"benchmark_compare_{stamp}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved comparison -> {path}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "hybrid"
    if arg == "compare":
        compare()
    else:
        run(arg)
