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
