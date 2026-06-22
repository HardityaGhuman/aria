"""
eval/answers.py
---------------
Generate grounded answers for the eval dataset and export them for RAGAS.

Runs in the APP venv. For each question it retrieves context (hybrid) and
generates an answer with the real pipeline, throttled to stay under the
configured tokens-per-minute budget (LLM_TOKENS_PER_MINUTE). It then computes
the cheap reference-based answer metric (answer_coverage) and writes a RAGAS
input file the isolated runner consumes — see backend/eval/ragas/run_ragas.py.

Run:
    python -m backend.eval.answers            # default subset
    python -m backend.eval.answers 8          # first 8 questions
"""
import json
import time
from datetime import datetime
from pathlib import Path

# pyrefly: ignore [missing-import]
import litellm

from backend.core.config import LLM_TOKENS_PER_MINUTE, MODEL_NAME, RETRIEVAL_TOP_K
from backend.core.llm import get_llm_response
from backend.eval import metrics
from backend.eval.dataset import load_eval_dataset
from backend.rag.strategies import STRATEGIES

RESULTS_DIR = Path(__file__).with_name("results")
# Kept small by default: RAGAS (downstream) is token-heavy, so a handful of
# questions keeps a run to a few minutes under the rate limit.
DEFAULT_N = 6


class TokenRateLimiter:
    """Sliding-window token budget so we never exceed the limit in any 60s
    window. Uses a safety margin against estimation error."""

    def __init__(self, tokens_per_minute: int = LLM_TOKENS_PER_MINUTE, margin: float = 0.8):
        self.budget = int(tokens_per_minute * margin)
        self.window = 60.0
        self._events: list[tuple[float, int]] = []

    def _used(self, now: float) -> int:
        self._events = [(t, n) for (t, n) in self._events if now - t < self.window]
        return sum(n for _, n in self._events)

    def acquire(self, tokens: int) -> None:
        while True:
            now = time.time()
            if self._used(now) + tokens <= self.budget or not self._events:
                self._events.append((now, tokens))
                return
            oldest = min(t for t, _ in self._events)
            time.sleep(max(self.window - (now - oldest) + 0.1, 0.5))


def _format_context(candidates) -> tuple[str, list[str]]:
    """Return (context block for the LLM, list of raw chunk texts for RAGAS)."""
    block = "\n\n".join(
        f"[Source: {c.metadata.get('source')}, chunk {c.metadata.get('chunk')}]\n{c.text}"
        for c in candidates
    )
    return block, [c.text for c in candidates]


def _estimate_tokens(question: str, context_block: str) -> int:
    """Conservative estimate: prompt tokens + system-prompt + completion allowance."""
    try:
        prompt = litellm.token_counter(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": question + "\n" + context_block}],
        )
    except Exception:
        prompt = len(question + context_block) // 4
    return prompt + 1000  # system prompt (~600) + answer (~400)


def generate_answers(dataset: list[dict] | None = None, n: int = DEFAULT_N,
                     strategy: str = "hybrid", k: int | None = None,
                     delay_seconds: float = 0.0) -> list[dict]:
    """Generate answers + contexts for an eval dataset (or the first n items).

    ``delay_seconds`` enforces a fixed pause between queries (a simple, predictable
    alternative to the token limiter for tight provider rate limits)."""
    if dataset is None:
        dataset = load_eval_dataset()[: n or None]
    k = k or RETRIEVAL_TOP_K
    limiter = TokenRateLimiter()

    samples = []
    for i, item in enumerate(dataset, start=1):
        question = item["question"]
        candidates = STRATEGIES[strategy](question)[:k]
        context_block, contexts = _format_context(candidates)

        if delay_seconds and i > 1:
            time.sleep(delay_seconds)
        limiter.acquire(_estimate_tokens(question, context_block))
        print(f"  [{i}/{len(dataset)}] {question[:60]}")
        answer = get_llm_response(question, context_block, history=[])

        samples.append({
            "question": question,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": item["ground_truth"],
            "expected_sections": item["expected_sections"],
            "difficulty": item.get("difficulty", "easy"),
            "answer_coverage": round(metrics.answer_coverage(answer, item["ground_truth"]), 3),
        })
    return samples


def export_ragas_input(samples: list[dict]) -> Path:
    """Write samples to the fixed path the RAGAS runner reads, plus a timestamped copy."""
    RESULTS_DIR.mkdir(exist_ok=True)
    payload = json.dumps(samples, indent=2, ensure_ascii=False)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"ragas_input_{stamp}.json"
    out.write_text(payload, encoding="utf-8")
    (RESULTS_DIR / "ragas_input.json").write_text(payload, encoding="utf-8")
    return out


def run(n: int = DEFAULT_N) -> Path:
    print(f"Generating answers for {n} questions (throttled to {LLM_TOKENS_PER_MINUTE} tok/min)...")
    samples = generate_answers(n=n)
    out = export_ragas_input(samples)

    cov = [s["answer_coverage"] for s in samples]
    print("\nReference-based answer metric (keyword coverage vs ground truth):")
    for s in samples:
        print(f"  {s['answer_coverage']:.2f}  [{s['difficulty']}] {s['question'][:55]}")
    print(f"\nAVG answer_coverage = {sum(cov) / len(cov):.2f}")
    print(f"RAGAS input -> {out}")
    print("Next, score with real RAGAS in the isolated venv:")
    print("    eval_venv/bin/python backend/eval/ragas/run_ragas.py")
    return out


if __name__ == "__main__":
    import sys

    run(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N)
