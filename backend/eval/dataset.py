"""
eval/dataset.py
---------------
Loader for the labeled evaluation dataset.

Each item has:
    question:          the user query
    ground_truth:      the expected correct answer (for answer-quality metrics)
    expected_sections: the handbook section(s) the answer should come from —
                       the meaningful retrieval ground truth for a single-document
                       corpus, where file-level sources are all the same
    expected_sources:  expected source filenames (kept for multi-document setups)
"""
import json
from pathlib import Path

DATASET_PATH = Path(__file__).with_name("eval_questions.json")

_REQUIRED_KEYS = ("question", "ground_truth", "expected_sections")


def load_eval_dataset(path: str | Path | None = None) -> list[dict]:
    """Load and validate the evaluation dataset."""
    dataset_path = Path(path) if path else DATASET_PATH
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list) or not data:
        raise ValueError(f"{dataset_path} must contain a non-empty list of items.")
    for i, item in enumerate(data):
        missing = [k for k in _REQUIRED_KEYS if k not in item]
        if missing:
            raise ValueError(f"Eval item {i} is missing keys: {missing}")
    return data


def get_questions(path: str | Path | None = None) -> list[str]:
    return [item["question"] for item in load_eval_dataset(path)]


def sample_by_difficulty(per_level: int = 5, path: str | Path | None = None) -> list[dict]:
    """Return up to ``per_level`` items from each difficulty (easy/moderate/hard),
    in difficulty order — a stratified subset for balanced evaluation runs."""
    dataset = load_eval_dataset(path)
    subset = []
    for level in ("easy", "moderate", "hard"):
        items = [d for d in dataset if d.get("difficulty", "easy") == level]
        subset.extend(items[:per_level])
    return subset
