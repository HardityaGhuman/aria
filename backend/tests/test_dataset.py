"""Schema + corpus-id validation for the eval dataset.

The corpus-id check is the guardrail: a typo'd expected_document_ids entry must
fail at test time, not silently zero out a retrieval metric at run time.
"""
import json

import pytest

from backend.eval import dataset


def _good_item(**over):
    item = {
        "id": "pto-tenure",
        "question": "PTO at 6 years tenure?",
        "ground_truth": "28 days.",
        "expected_document_ids": ["time-and-leave/working-hours-and-pto.md"],
        "department": "time-and-leave",
        "difficulty": "moderate",
        "query_type": "single_doc",
    }
    item.update(over)
    return item


def _write(tmp_path, items):
    p = tmp_path / "ds.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return p


def test_loads_valid_dataset(tmp_path):
    p = _write(tmp_path, [_good_item()])
    assert dataset.load_eval_dataset(p)[0]["id"] == "pto-tenure"


def test_missing_key_rejected(tmp_path):
    bad = _good_item()
    del bad["expected_document_ids"]
    p = _write(tmp_path, [bad])
    with pytest.raises(ValueError):
        dataset.load_eval_dataset(p)


def test_bad_query_type_rejected(tmp_path):
    p = _write(tmp_path, [_good_item(query_type="trivia")])
    with pytest.raises(ValueError):
        dataset.load_eval_dataset(p)


def test_empty_expected_docs_rejected(tmp_path):
    p = _write(tmp_path, [_good_item(expected_document_ids=[])])
    with pytest.raises(ValueError):
        dataset.load_eval_dataset(p)


def test_corpus_document_ids_includes_known_doc():
    ids = dataset.corpus_document_ids()
    assert "time-and-leave/working-hours-and-pto.md" in ids
    assert "finance/salary-bands.csv" in ids


def test_validate_against_corpus_flags_unknown_id(tmp_path):
    bad = _good_item(expected_document_ids=["nope/does-not-exist.md"])
    errors = dataset.validate_against_corpus([bad])
    assert errors and "nope/does-not-exist.md" in errors[0]


def test_validate_against_corpus_clean_for_real_id():
    assert dataset.validate_against_corpus([_good_item()]) == []


def test_real_dataset_loads_and_resolves_to_corpus():
    # The shipped dataset must be structurally valid AND every expected document
    # id must point at a real corpus file — no typos, no stale ids.
    data = dataset.load_eval_dataset()
    assert len(data) >= 20
    assert dataset.validate_against_corpus(data) == []


def test_real_dataset_covers_hard_query_types():
    data = dataset.load_eval_dataset()
    qtypes = [d["query_type"] for d in data]
    assert qtypes.count("vocab_gap") >= 4
    assert qtypes.count("cross_doc") >= 3
    assert qtypes.count("tabular") >= 3
    departments = {d["department"] for d in data}
    assert departments >= {"hr", "finance", "it", "time-and-leave", "benefits", "legal-compliance", "people-career"}
