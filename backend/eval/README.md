# Evaluation

Two complementary layers, run offline.

## 1. Retrieval (reference-based) — app venv, no LLM
Scores retrieval against the labeled dataset (`eval_questions.json`, tagged
easy/moderate/hard with `expected_sections`). Pure local embeddings + BM25, so
it's fast and not rate-limited.

```bash
python -m backend.eval.benchmark            # hybrid
python -m backend.eval.benchmark compare    # vector vs bm25 vs hybrid, saved to results/
```
Metrics: `recall@k`, `hit@k`, `mrr`, `context_hit_rate`, broken down by difficulty.

## 2. Answer quality + RAGAS
RAGAS's dependencies (its LangChain pins) conflict irreconcilably with the app's,
so RAGAS lives in a **separate virtualenv** and the app hands it data via JSON.

### a. Export answers (app venv) — throttled to the configured token budget
```bash
python -m backend.eval.answers 6     # generate answers + contexts for 6 questions
```
Throttle is `LLM_TOKENS_PER_MINUTE` (config, default 12000).
Writes `results/ragas_input.json` and prints the cheap reference-based answer
metric (`answer_coverage` = keyword overlap with ground truth).

### b. Score with real RAGAS (isolated eval venv)
```bash
python -m venv eval_venv
eval_venv/bin/pip install -r backend/eval/ragas/requirements.txt   # one-time
eval_venv/bin/python backend/eval/ragas/run_ragas.py 4
```
Runs the genuine `ragas` library (faithfulness, answer_relevancy,
context_precision, context_recall) using the same Groq judge model and local
MiniLM embeddings. Serial with backoff, so the Groq rate limit is respected via
retries rather than bursting. Scores saved to `results/ragas_scores_*.json`.

> Keep RAGAS subsets small (a handful of questions): it is token-heavy, so under
> a constrained provider rate limit larger runs take several minutes.

`results/` is gitignored — the harness is tracked, individual runs are not.
