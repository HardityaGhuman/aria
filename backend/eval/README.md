# Evaluation

Two offline layers, scored against the real multi-document corpus.

## 1. Retrieval (document-level, no LLM) — app venv

Scores retrieval against `eval_questions.json` (hand-authored, tagged
easy/moderate/hard and by query_type). A retrieved chunk is relevant iff its
`metadata["source"]` exactly equals an `expected_document_ids` entry.

```bash
python -m backend.eval.benchmark            # hybrid
python -m backend.eval.benchmark vector     # one strategy
python -m backend.eval.benchmark compare    # vector vs bm25 vs hybrid -> results/
```

Metrics: `doc_recall@k`, `doc_precision@k` (the noise metric for the document
viewer), `doc_hit@k`, `doc_mrr` — reported overall, by difficulty, and by
query_type (`single_doc`/`cross_doc`/`vocab_gap`/`tabular`) so weak corners are
visible. No LLM calls, so it's fast and not rate-limited. Eval uses the raw
strategy over the full corpus (no RBAC filter) to isolate ranking quality.

## 2. Answer quality + RAGAS

RAGAS's LangChain pins conflict with the app's deps, so it runs in a separate
virtualenv fed JSON by the app.

### a. Export (app venv) — throttled to the token budget
```bash
python -m backend.eval.answers 6     # answers + contexts for 6 questions
```
Writes `results/ragas_input.json` and prints `answer_coverage` (cheap keyword
overlap vs ground truth).

### b. Score (isolated eval venv)
```bash
python -m venv eval_venv
eval_venv/bin/pip install -r backend/eval/ragas/requirements.txt   # one-time
eval_venv/bin/python backend/eval/ragas/run_ragas.py 4
```
Real RAGAS: faithfulness, answer_relevancy, context_precision, context_recall.

### Stratified combined run (retrieval + answers in one)
```bash
python -m backend.eval.run_eval 3    # 3 questions per difficulty
```

> Keep RAGAS subsets small — it is token-heavy under the provider rate limit.
> `results/` is gitignored: the harness is tracked, individual runs are not.
