"""Tests for the retrieval where-clause helpers (pure helpers, no DB).

Tier gating was removed from strategies.py (moved to the retriever's
partition_by_tier). These tests cover: content_type exclusion, status
exclusion, and region filtering — the three axes that still live here.
"""
from backend.rag import strategies


def test_where_excludes_structural_chunks_and_status_no_region():
    # No regions: should produce content_type + status only.
    result = strategies._vector_where()
    assert result == {
        "$and": [
            {"content_type": {"$nin": ["toc", "overview"]}},
            {"status": {"$ne": "superseded"}},
        ]
    }


def test_where_none_region_includes_status_no_region_no_tier():
    """_vector_where(None): content_type + status, no region, no access_tier."""
    result = strategies._vector_where(None)
    assert "$and" in result
    clauses = result["$and"]
    assert {"content_type": {"$nin": ["toc", "overview"]}} in clauses
    assert {"status": {"$ne": "superseded"}} in clauses
    # No tier or region clause
    for clause in clauses:
        assert "access_tier" not in clause
        assert "region" not in clause


def test_where_adds_region_filter_when_regions_given():
    result = strategies._vector_where(["global", "us"])
    assert result == {
        "$and": [
            {"content_type": {"$nin": ["toc", "overview"]}},
            {"status": {"$ne": "superseded"}},
            {"region": {"$in": ["global", "us"]}},
        ]
    }


def test_where_with_regions_includes_status_clause_no_tier():
    """status exclusion must always appear; no access_tier clause ever."""
    result = strategies._vector_where(["global", "us"])
    clauses = result["$and"]
    assert {"status": {"$ne": "superseded"}} in clauses
    assert {"region": {"$in": ["global", "us"]}} in clauses
    for clause in clauses:
        assert "access_tier" not in clause


def test_region_allowed():
    # Region in allowed list → True
    assert strategies._region_allowed({"region": "global"}, ["global", "us"]) is True
    assert strategies._region_allowed({"region": "us"}, ["global", "us"]) is True
    # Region NOT in allowed list → False
    assert strategies._region_allowed({"region": "india"}, ["global", "us"]) is False
    # None allowed_regions = no restriction (eval harness)
    assert strategies._region_allowed({"region": "india"}, None) is True
    assert strategies._region_allowed({}, None) is True


def test_status_active():
    assert strategies._status_active({"status": "superseded"}) is False
    assert strategies._status_active({"status": "active"}) is True
    # Missing status key → defaults to active (safe default)
    assert strategies._status_active({}) is True
    # Any value other than "superseded" is active
    assert strategies._status_active({"status": "draft"}) is True
