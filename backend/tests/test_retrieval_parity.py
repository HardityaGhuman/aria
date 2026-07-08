import backend.rag.bm25 as bm25
from backend.rag.retriever import retrieve
from backend.rag.vector_repository import InMemoryVectorRepository, ChunkInput, set_repository


def _emb(*v):
    e = [0.0] * 3
    for i, x in enumerate(v):
        e[i] = x
    return e


def _ci(source, idx, emb, region="global", tier="all", status="active", ctype=""):
    meta = {"source": source, "chunk": idx, "region": region, "access_tier": tier,
            "status": status, "content_type": ctype, "department": "hr", "parent_section": "S"}
    return ChunkInput(source, idx, f"text {source} {idx}", emb, meta, region, status, ctype)


def _seed():
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/a.md", "hr", "h", "cv", "ev", [
        _ci("hr/a.md", 1, _emb(1, 0, 0)),
        _ci("hr/a.md", 2, _emb(0, 1, 0), tier="hr_only"),
    ])
    set_repository(repo)
    bm25.invalidate_bm25()
    return repo


def teardown_function():
    set_repository(None)
    bm25.invalidate_bm25()


def test_hybrid_returns_allowed_and_partitions_tier():
    _seed()
    got = retrieve("text hr", strategy="hybrid", allowed_tiers=["all"], allowed_regions=["global"])
    assert got.status == "ok"
    assert got.sources and all(s["access_tier"] == "all" for s in got.sources)  # hr_only partitioned out


def test_blocked_when_only_restricted_matches():
    _seed()
    # seed only an hr_only chunk → an 'all'-tier caller must be blocked
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/x.md", "hr", "h", "cv", "ev", [_ci("hr/x.md", 1, _emb(1, 0, 0), tier="hr_only")])
    set_repository(repo); bm25.invalidate_bm25()
    blocked = retrieve("text hr", strategy="hybrid", allowed_tiers=["all"], allowed_regions=["global"])
    assert blocked.status == "blocked"
