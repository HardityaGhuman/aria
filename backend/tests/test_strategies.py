"""Tests for the retrieval where-clause + tier gate (pure helpers, no DB)."""
from backend.rag import strategies


def test_where_excludes_structural_chunks_when_no_tiers():
    # With no tiers AND no regions, should still always include status exclusion.
    result = strategies._vector_where(None)
    # Must exclude superseded regardless
    assert result == {
        "$and": [
            {"content_type": {"$nin": ["toc", "overview"]}},
            {"status": {"$ne": "superseded"}},
        ]
    }


def test_where_adds_access_tier_filter_when_tiers_given():
    result = strategies._vector_where(["all"])
    assert result == {
        "$and": [
            {"content_type": {"$nin": ["toc", "overview"]}},
            {"status": {"$ne": "superseded"}},
            {"access_tier": {"$in": ["all"]}},
        ]
    }


def test_where_adds_region_filter_when_regions_given():
    result = strategies._vector_where(["all"], ["global", "us"])
    assert result == {
        "$and": [
            {"content_type": {"$nin": ["toc", "overview"]}},
            {"status": {"$ne": "superseded"}},
            {"access_tier": {"$in": ["all"]}},
            {"region": {"$in": ["global", "us"]}},
        ]
    }


def test_where_with_tiers_and_regions_includes_status_clause():
    """status exclusion must always appear, regardless of other filters."""
    result = strategies._vector_where(["all"], ["global", "us"])
    clauses = result["$and"]
    assert {"status": {"$ne": "superseded"}} in clauses
    assert {"access_tier": {"$in": ["all"]}} in clauses
    assert {"region": {"$in": ["global", "us"]}} in clauses


def test_where_none_none_includes_status_no_tier_no_region():
    """_vector_where(None, None): content_type + status, no tier, no region."""
    result = strategies._vector_where(None, None)
    # Must be $and with exactly content_type and status
    assert "$and" in result
    clauses = result["$and"]
    assert {"content_type": {"$nin": ["toc", "overview"]}} in clauses
    assert {"status": {"$ne": "superseded"}} in clauses
    # No tier or region clause
    for clause in clauses:
        assert "access_tier" not in clause
        assert "region" not in clause


def test_tier_gate():
    assert strategies._tier_allowed({"access_tier": "all"}, ["all"]) is True
    assert strategies._tier_allowed({"access_tier": "hr_only"}, ["all"]) is False
    assert strategies._tier_allowed({"access_tier": "hr_only"}, ["all", "hr_only"]) is True
    # no restriction (offline eval) sees everything
    assert strategies._tier_allowed({"access_tier": "hr_only"}, None) is True


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
