"""Tests for the BM25-path filter helpers (pure helpers, no DB).

The vector path's content_type/status/region filtering now runs inside the
`VectorRepository` (§11 storage swap) and is covered by
test_vector_repository_inmemory + test_retrieval_parity. The Chroma-specific
`_vector_where` builder was deleted with the Chroma backend. What remains here
are the helpers `bm25_search` still applies in app code: region + status gates.
"""
from backend.rag import strategies


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
