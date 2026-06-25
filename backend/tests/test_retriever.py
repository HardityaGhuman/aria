import backend.rag.retriever as rv
from backend.rag.schema import Candidate
from backend.rag.retriever import partition_by_tier, blocked_contact


def _c(tier):
    return Candidate(chunk_id=f"id-{tier}", text=f"text-{tier}", metadata={"access_tier": tier})


def test_partition_splits_allowed_and_blocked():
    cands = [_c("all"), _c("hr_only"), _c("manager")]
    allowed, blocked = partition_by_tier(cands, ["all"])
    assert [c.metadata["access_tier"] for c in allowed] == ["all"]
    assert {c.metadata["access_tier"] for c in blocked} == {"hr_only", "manager"}


def test_partition_none_allows_everything():
    cands = [_c("all"), _c("hr_only")]
    allowed, blocked = partition_by_tier(cands, None)
    assert len(allowed) == 2 and blocked == []


def test_blocked_contact_prefers_hr_over_manager():
    assert blocked_contact([_c("manager"), _c("hr_only")]) == "HR"
    assert blocked_contact([_c("manager")]) == "your manager"
    assert blocked_contact([]) == "HR"


def test_retrieve_excludes_blocked_tier_from_context_and_sources(monkeypatch):
    """Retrieve-level invariant: a blocked-tier chunk never reaches text/sources,
    even when an allowed chunk is present. Guards against a regression that
    builds context from candidates[:n] instead of allowed[:n]."""
    class _Coll:
        def count(self): return 5
    monkeypatch.setattr(rv, "get_collection", lambda: _Coll())
    cands = [
        Candidate(chunk_id="a", text="ALLOWED all-tier text", metadata={"access_tier": "all", "source": "x.md", "chunk": 1}),
        Candidate(chunk_id="b", text="SECRET hr_only salary text", metadata={"access_tier": "hr_only", "source": "salary-bands.csv", "chunk": 1}),
    ]
    monkeypatch.setitem(rv.STRATEGIES, "hybrid", lambda query, allowed_regions=None: cands)

    r = rv.retrieve("q", strategy="hybrid", allowed_tiers=["all"])
    assert r.status == "ok"
    assert "SECRET" not in r.text
    assert all(s["access_tier"] == "all" for s in r.sources)
    assert not any(s["source"] == "salary-bands.csv" for s in r.sources)


def test_retrieve_blocked_when_only_restricted_matches(monkeypatch):
    """When every match is blocked, retrieve returns status=blocked with the
    HR contact and an empty text/sources payload."""
    class _Coll:
        def count(self): return 5
    monkeypatch.setattr(rv, "get_collection", lambda: _Coll())
    cands = [Candidate(chunk_id="b", text="SECRET", metadata={"access_tier": "hr_only", "source": "salary-bands.csv", "chunk": 1})]
    monkeypatch.setitem(rv.STRATEGIES, "hybrid", lambda query, allowed_regions=None: cands)
    r = rv.retrieve("q", strategy="hybrid", allowed_tiers=["all"])
    assert r.status == "blocked" and r.blocked_contact == "HR"
    assert r.text == "" and r.sources == []
