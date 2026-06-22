"""
backend/eval/ragas/run_ragas.py
-------------------------------
Standalone RAGAS evaluation. Runs in the ISOLATED eval venv (real ragas +
its own LangChain pins), NOT the app venv.

It reads the RAGAS input produced by the app (backend/eval/answers.py) and
scores it on the four ground-truth metrics:

    faithfulness        — is the answer supported by the retrieved context?
    answer_relevancy    — does the answer address the question?
    context_precision   — are the retrieved chunks relevant (ranking-aware)?
    context_recall      — do the chunks cover the ground-truth answer?

The judge is the same Groq model the app uses; embeddings are local MiniLM.
Runs serially (max_workers=1) with retries so the provider rate limit is
respected via backoff rather than bursting.

Usage (after exporting input with backend.eval.answers):
    eval_venv/bin/python backend/eval/ragas/run_ragas.py            # default subset
    eval_venv/bin/python backend/eval/ragas/run_ragas.py 4          # first 4 samples
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve()
RESULTS_DIR = HERE.parents[1] / "results"
BACKEND_ENV = HERE.parents[2] / ".env"  # backend/.env

# Default RAGAS subset — kept small because RAGAS is token-heavy and the
# provider rate limit makes larger runs slow.
DEFAULT_N = 4


def main(n: int = DEFAULT_N) -> None:
    load_dotenv(BACKEND_ENV)
    if not os.getenv("GROQ_API_KEY"):
        sys.exit("GROQ_API_KEY not found in backend/.env")

    input_path = RESULTS_DIR / "ragas_input.json"
    if not input_path.exists():
        sys.exit(f"{input_path} not found. Run: python -m backend.eval.answers (app venv) first.")

    from datasets import disable_progress_bars
    disable_progress_bars()
    from langchain_groq import ChatGroq
    from langchain_huggingface import HuggingFaceEmbeddings
    from ragas import EvaluationDataset, RunConfig, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    data = json.loads(input_path.read_text(encoding="utf-8"))[:n]
    print(f"RAGAS on {len(data)} samples (serial, with backoff for rate limits)...")

    samples = [
        SingleTurnSample(
            user_input=d["question"],
            response=d["answer"],
            retrieved_contexts=d["contexts"],
            reference=d["ground_truth"],
        )
        for d in data
    ]
    dataset = EvaluationDataset(samples=samples)

    model = os.getenv("MODEL_NAME", "groq/llama-3.3-70b-versatile").split("/", 1)[-1]
    llm = LangchainLLMWrapper(ChatGroq(model=model, temperature=0))
    embeddings = LangchainEmbeddingsWrapper(
        HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2", model_kwargs={"local_files_only": True})
    )
    metric_list = [faithfulness, answer_relevancy, context_precision, context_recall]

    result = evaluate(
        dataset=dataset,
        metrics=metric_list,
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=1, timeout=240, max_retries=15, max_wait=90),
        show_progress=False,
    )

    df = result.to_pandas()
    metric_cols = [m.name for m in metric_list if m.name in df.columns]
    aggregate = {col: round(float(df[col].mean()), 3) for col in metric_cols}

    print("\nRAGAS scores (aggregate):")
    for col, val in aggregate.items():
        print(f"  {col:<18} {val:.3f}")

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"ragas_scores_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "model": model,
                "n": len(data),
                "aggregate": aggregate,
                "per_sample": json.loads(df.to_json(orient="records")),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved -> {out}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N)
