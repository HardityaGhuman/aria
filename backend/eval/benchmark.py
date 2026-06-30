"""
eval/benchmark.py
-----------------
Batch retrieval evaluation over the labeled dataset.

For each question it runs a retrieval strategy, compares the retrieved sections
against the expected ones, and aggregates reference-based metrics:

    recall@k          — fraction of expected sections retrieved
    hit@k             — did any expected section make the top-k
    mrr               — reciprocal rank of the first relevant chunk
    context_hit_rate  — fraction of retrieved chunks that were relevant

Results are broken down by difficulty (easy / moderate / hard) so retrieval
degradation on harder, indirectly-phrased queries is visible, and saved to
``backend/eval/results/``.

This benchmark makes NO LLM API calls — strategies use local embeddings + BM25,
so it is not subject to model rate limits. (Answer-quality / RAGAS metrics in
Phase 4 will be, and will throttle accordingly.)

Run:
    python -m backend.eval.benchmark            # default (hybrid)
    python -m backend.eval.benchmark vector     # one strategy
    python -m backend.eval.benchmark compare    # all strategies, saved to results/
"""
import json
from datetime import datetime
from pathlib import Path

from backend.core.config import RETRIEVAL_TOP_K
from backend.eval import metrics
from backend.eval.dataset import load_eval_dataset
from backend.rag.strategies import STRATEGIES

RESULTS_DIR = Path(__file__).with_name("results")
DIFFICULTY_ORDER = ["easy", "moderate", "hard"]


def _retrieved_sections(query: str, strategy: str, k: int) -> list[str]:
    """Top-k retrieved chunks' sections, in rank order (best first)."""
    candidates = STRATEGIES[strategy](query)[:k]
    return [c.metadata.get("parent_section", "") for c in candidates]


def evaluate_retrieval(dataset: list[dict] | None = None, strategy: str = "hybrid", k: int | None = None) -> list[dict]:
    """Per-question retrieval scores for one strategy."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}'. Choose from {list(STRATEGIES)}.")
    dataset = dataset if dataset is not None else load_eval_dataset()
    k = k or RETRIEVAL_TOP_K

    rows = []
    for item in dataset:
        retrieved = _retrieved_sections(item["question"], strategy, k)
        expected = item["expected_sections"]
        rows.append({
            "question": item["question"],
            "difficulty": item.get("difficulty", "easy"),
            "expected": expected,
            "retrieved": retrieved,
            "recall": metrics.section_recall(retrieved, expected),
            "hit": metrics.hit_at_k(retrieved, expected),
            "mrr": metrics.reciprocal_rank(retrieved, expected),
            "hit_rate": metrics.context_hit_rate(retrieved, expected),
        })
    return rows


def aggregate(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "questions": len(rows),
        "recall@k": sum(r["recall"] for r in rows) / n,
        "hit@k": sum(r["hit"] for r in rows) / n,
        "mrr": sum(r["mrr"] for r in rows) / n,
        "context_hit_rate": sum(r["hit_rate"] for r in rows) / n,
    }


def aggregate_by_difficulty(rows: list[dict]) -> dict:
    out = {}
    for level in DIFFICULTY_ORDER:
        subset = [r for r in rows if r["difficulty"] == level]
        if subset:
            out[level] = aggregate(subset)
    return out


def _print_rows(rows: list[dict]) -> None:
    print(f"{'diff':>8} {'recall':>6} {'hit':>4} {'mrr':>5}  question")
    for r in rows:
        miss = "" if r["hit"] else "   <-- MISS"
        print(f"{r['difficulty']:>8} {r['recall']:>6.2f} {r['hit']:>4.0f} {r['mrr']:>5.2f}  {r['question'][:50]}{miss}")


def _print_breakdown(by_diff: dict) -> None:
    print(f"{'level':>9} | {'recall@k':>8} | {'hit@k':>6} | {'mrr':>5} | {'ctx_hit':>7} | {'n':>3}")
    print("-" * 56)
    for level in DIFFICULTY_ORDER:
        if level in by_diff:
            a = by_diff[level]
            print(f"{level:>9} | {a['recall@k']:>8.2f} | {a['hit@k']:>6.2f} | {a['mrr']:>5.2f} | {a['context_hit_rate']:>7.2f} | {a['questions']:>3}")


def run(strategy: str = "hybrid") -> dict:
    rows = evaluate_retrieval(strategy=strategy)
    overall = aggregate(rows)
    by_diff = aggregate_by_difficulty(rows)

    print(f"\nRetrieval benchmark — strategy={strategy}, k={RETRIEVAL_TOP_K}, n={overall['questions']}")
    print("-" * 80)
    _print_rows(rows)
    print("-" * 80)
    _print_breakdown(by_diff)
    print(
        f"\nOVERALL  recall@k={overall['recall@k']:.2f}  hit@k={overall['hit@k']:.2f}  "
        f"mrr={overall['mrr']:.2f}  context_hit_rate={overall['context_hit_rate']:.2f}"
    )
    return overall


def save_results(results: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"retrieval_{stamp}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_comparison(save: bool = True) -> dict:
    dataset = load_eval_dataset()
    counts = {}
    for item in dataset:
        level = item.get("difficulty", "easy")
        counts[level] = counts.get(level, 0) + 1

    results = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "k": RETRIEVAL_TOP_K,
        "n": len(dataset),
        "difficulty_counts": counts,
        "strategies": {},
    }

    print(f"\nStrategy comparison — n={len(dataset)} {counts}, k={RETRIEVAL_TOP_K}")
    print(f"{'strategy':>8} | {'recall@k':>8} | {'hit@k':>6} | {'mrr':>5} | {'ctx_hit':>7}")
    print("-" * 48)
    for strategy in STRATEGIES:
        rows = evaluate_retrieval(dataset=dataset, strategy=strategy)
        overall = aggregate(rows)
        results["strategies"][strategy] = {
            "overall": overall,
            "by_difficulty": aggregate_by_difficulty(rows),
            "rows": rows,
        }
        print(
            f"{strategy:>8} | {overall['recall@k']:>8.2f} | {overall['hit@k']:>6.2f} | "
            f"{overall['mrr']:>5.2f} | {overall['context_hit_rate']:>7.2f}"
        )

    print("\nBy difficulty (hybrid):")
    _print_breakdown(results["strategies"]["hybrid"]["by_difficulty"])

    if save:
        path = save_results(results)
        print(f"\nSaved metrics -> {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
    return results


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else "hybrid"
    if arg == "compare":
        run_comparison()
    else:
        run(arg)
