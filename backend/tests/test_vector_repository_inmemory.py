from backend.rag.vector_repository import InMemoryVectorRepository, ChunkInput


def _chunk(source, idx, emb, region="global", status="active", ctype=""):
    meta = {"source": source, "chunk": idx, "region": region,
            "status": status, "content_type": ctype, "access_tier": "all"}
    return ChunkInput(document_id=source, chunk_index=idx, content=f"{source}#{idx}",
                      embedding=emb, metadata=meta, region=region,
                      content_status=status, content_type=ctype)


def test_query_ranks_by_cosine_and_applies_filters():
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/a.md", "hr", "h1", "cv1", "ev1", [
        _chunk("hr/a.md", 1, [1.0, 0.0, 0.0]),
        _chunk("hr/a.md", 2, [0.0, 1.0, 0.0]),
    ])
    repo.upsert_document("hr/old.md", "hr", "h2", "cv1", "ev1", [
        _chunk("hr/old.md", 1, [1.0, 0.0, 0.0], status="superseded"),
    ])
    got = repo.query([1.0, 0.0, 0.0], k=10, allowed_regions=["global"],
                     exclude_content_types=["toc", "overview"])
    ids = [c.chunk_id for c in got]
    assert ids[0] == "hr/a.md:1"            # closest by cosine
    assert "hr/old.md:1" not in ids         # superseded excluded
    assert got[0].distance is not None and got[0].distance < 1e-6


def test_query_region_filter():
    repo = InMemoryVectorRepository()
    repo.upsert_document("in/x.md", "hr", "h", "cv1", "ev1", [
        _chunk("in/x.md", 1, [1.0, 0.0, 0.0], region="india"),
    ])
    assert repo.query([1.0, 0.0, 0.0], 10, ["global"], []) == []
    assert len(repo.query([1.0, 0.0, 0.0], 10, ["global", "india"], [])) == 1


def test_upsert_replaces_and_metadata_roundtrips():
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/a.md", "hr", "h1", "cv1", "ev1", [_chunk("hr/a.md", 1, [1.0, 0.0, 0.0])])
    repo.upsert_document("hr/a.md", "hr", "h2", "cv1", "ev1", [_chunk("hr/a.md", 1, [0.0, 1.0, 0.0])])
    assert repo.count() == 1                         # replaced, not appended
    assert repo.active_version_meta("hr/a.md") == ("h2", "cv1")
    c = repo.query([0.0, 1.0, 0.0], 1, ["global"], [])[0]
    assert c.metadata["source"] == "hr/a.md"        # verbatim metadata
    assert c.metadata["access_tier"] == "all"


def test_all_chunks_excludes_content_types_and_indexed_sources():
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/a.md", "hr", "h", "cv1", "ev1", [
        _chunk("hr/a.md", 1, [1.0, 0.0, 0.0]),
        _chunk("hr/a.md", 2, [1.0, 0.0, 0.0], ctype="toc"),
    ])
    texts = [c.text for c in repo.all_chunks(exclude_content_types=["toc", "overview"])]
    assert "hr/a.md#1" in texts and "hr/a.md#2" not in texts
    assert repo.indexed_sources() == {"hr/a.md"}


def test_delete_document():
    repo = InMemoryVectorRepository()
    repo.upsert_document("hr/a.md", "hr", "h", "cv1", "ev1", [_chunk("hr/a.md", 1, [1.0, 0.0, 0.0])])
    assert repo.delete_document("hr/a.md") == 1
    assert repo.count() == 0
