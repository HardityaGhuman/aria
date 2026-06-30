"""
eval/run_eval.py
----------------
Stratified evaluation run (equal questions per difficulty). Computes retrieval
metrics + the reference-based answer metric over the subset, saves a combined
metrics file, and exports the RAGAS input for the isolated runner.

Run:
    python -m backend.eval.run_eval          # 5 per difficulty
    python -m backend.eval.run_eval 3        # 3 per difficulty
"""
import json
from datetime import datetime
from pathlib import Path

from backend.core.config import RETRIEVAL_TOP_K
from backend.eval import answers, benchmark
from backend.eval.dataset import sample_by_difficulty

RESULTS_DIR = Path(__file__).with_name("results")
DIFFICULTIES = ("easy", "moderate", "hard")


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def run(per_level: int = 5, strategy: str = "hybrid", delay_seconds: float = 12) -> Path:
    subset = sample_by_difficulty(per_level)
    counts = {lvl: sum(1 for d in subset if d.get("difficulty") == lvl) for lvl in DIFFICULTIES}
    print(f"Stratified eval — {counts}, strategy={strategy}, k={RETRIEVAL_TOP_K}, gap={delay_seconds}s\n")

    # 1. Retrieval metrics (no LLM, instant).
    retr_rows = benchmark.evaluate_retrieval(dataset=subset, strategy=strategy)

    # 2. Answers + reference-based answer metric (paced LLM calls).
    samples = answers.generate_answers(dataset=subset, strategy=strategy, delay_seconds=delay_seconds)
    answers.export_ragas_input(samples)

    # Per-question combined rows (subset order is identical in both passes).
    rows = [
        {
            "id": rr["id"],
            "question": rr["question"],
            "difficulty": rr["difficulty"],
            "query_type": rr["query_type"],
            "doc_recall": rr["doc_recall"],
            "doc_precision": rr["doc_precision"],
            "doc_hit": rr["doc_hit"],
            "doc_mrr": rr["doc_mrr"],
            "answer_coverage": s["answer_coverage"],
        }
        for rr, s in zip(retr_rows, samples)
    ]

    metric_names = ("doc_recall", "doc_precision", "doc_hit", "doc_mrr", "answer_coverage")

    def by_diff(metric: str) -> dict:
        return {
            lvl: _avg([r[metric] for r in rows if r["difficulty"] == lvl])
            for lvl in DIFFICULTIES
        }

    combined = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "strategy": strategy,
        "k": RETRIEVAL_TOP_K,
        "per_level": per_level,
        "counts": counts,
        "overall": {m: _avg([r[m] for r in rows]) for m in metric_names},
        "by_difficulty": {m: by_diff(m) for m in metric_names},
        "rows": rows,
    }

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"eval_metrics_{stamp}.json"
    path.write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary.
    print("\n           | recall | prec | hit  | mrr  | ans_cov")
    print("-" * 56)
    for lvl in DIFFICULTIES:
        b = {m: combined["by_difficulty"][m][lvl] for m in metric_names}
        print(f"{lvl:>10} | {b['doc_recall']:.2f}   | {b['doc_precision']:.2f} | "
              f"{b['doc_hit']:.2f} | {b['doc_mrr']:.2f} | {b['answer_coverage']:.2f}")
    o = combined["overall"]
    print("-" * 56)
    print(f"{'overall':>10} | {o['doc_recall']:.2f}   | {o['doc_precision']:.2f} | "
          f"{o['doc_hit']:.2f} | {o['doc_mrr']:.2f} | {o['answer_coverage']:.2f}")
    print(f"\nSaved combined metrics -> {path}")
    print(f"RAGAS next (isolated venv): eval_venv/bin/python backend/eval/ragas/run_ragas.py {len(rows)}")
    return path


if __name__ == "__main__":
    import sys

    per = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 12
    run(per, delay_seconds=gap)
