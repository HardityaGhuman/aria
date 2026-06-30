"""
eval/dataset.py
---------------
Loader + validator for the labeled evaluation dataset (document-level).

Each item:
    id:                    stable slug for reports/tests
    question:              the user query
    ground_truth:          expected answer (RAGAS + cheap answer_coverage)
    expected_document_ids: list of stable source paths (= Chroma 'source' id);
                           the primary retrieval ground truth
    department:            for per-department recall reporting
    difficulty:            easy | moderate | hard
    query_type:            single_doc | cross_doc | vocab_gap | tabular
"""
import json
import os
from pathlib import Path

from backend.core.config import DOCS_PATH
from backend.rag.loaders import SUPPORTED_EXTENSIONS

DATASET_PATH = Path(__file__).with_name("eval_questions.json")

REQUIRED_KEYS = (
    "id",
    "question",
    "ground_truth",
    "expected_document_ids",
    "department",
    "difficulty",
    "query_type",
)
VALID_DIFFICULTIES = {"easy", "moderate", "hard"}
VALID_QUERY_TYPES = {"single_doc", "cross_doc", "vocab_gap", "tabular"}


def _validate_item(i: int, item: dict) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in item]
    if missing:
        raise ValueError(f"Eval item {i} ({item.get('id', '?')}) missing keys: {missing}")
    if not isinstance(item["expected_document_ids"], list) or not item["expected_document_ids"]:
        raise ValueError(f"Eval item {i} ({item['id']}): expected_document_ids must be a non-empty list")
    if item["difficulty"] not in VALID_DIFFICULTIES:
        raise ValueError(f"Eval item {i} ({item['id']}): bad difficulty {item['difficulty']!r}")
    if item["query_type"] not in VALID_QUERY_TYPES:
        raise ValueError(f"Eval item {i} ({item['id']}): bad query_type {item['query_type']!r}")


def load_eval_dataset(path: str | Path | None = None) -> list[dict]:
    """Load and structurally validate the evaluation dataset."""
    dataset_path = Path(path) if path else DATASET_PATH
    with open(dataset_path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise ValueError(f"{dataset_path} must contain a non-empty list of items.")
    for i, item in enumerate(data):
        _validate_item(i, item)
    return data


def corpus_document_ids(docs_root: str | Path | None = None) -> set[str]:
    """Every real corpus document id (path relative to the docs root), for the
    supported extensions. Used to verify a question's expected_document_ids
    actually exist."""
    root = Path(docs_root) if docs_root else Path(DOCS_PATH)
    ids: set[str] = set()
    for dirpath, _dirs, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                ids.add(rel.replace(os.sep, "/"))
    return ids


def validate_against_corpus(dataset: list[dict], docs_root: str | Path | None = None) -> list[str]:
    """Return human-readable errors for any expected_document_ids that do not
    resolve to a real corpus file. Empty list = clean."""
    known = corpus_document_ids(docs_root)
    errors = []
    for item in dataset:
        for doc_id in item.get("expected_document_ids", []):
            if doc_id not in known:
                errors.append(f"{item.get('id', '?')}: unknown document id {doc_id!r}")
    return errors


def get_questions(path: str | Path | None = None) -> list[str]:
    return [item["question"] for item in load_eval_dataset(path)]


def sample_by_difficulty(per_level: int = 5, path: str | Path | None = None) -> list[dict]:
    """Up to ``per_level`` items from each difficulty, in difficulty order —
    a stratified subset for balanced runs."""
    data = load_eval_dataset(path)
    subset = []
    for level in ("easy", "moderate", "hard"):
        items = [d for d in data if d.get("difficulty") == level]
        subset.extend(items[:per_level])
    return subset
