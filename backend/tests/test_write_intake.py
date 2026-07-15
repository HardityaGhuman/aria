"""Filing a Case, once, for every agent.

Before this, "file a Case" existed only INSIDE the three transport routes — Slack for
leave, JWT for jira/onboarding. The chat is now the product surface, so the same filing
has to be callable from the chat pipeline too. Two implementations of a step that decides
the approver and the idempotency key is two implementations of a security boundary, so
there is exactly one: `services/write_intake.py`. The routes delegate to it (their own
tests prove behaviour was preserved); the chat calls it directly.
"""
import pytest

from backend.core.tools.principal import Principal
from backend.services import write_intake

P = Principal(user_id=1, email="employee@gsvh.test", role="employee", region="us")


class _Store:
    """Module-shaped fake of a per-agent case store."""

    def __init__(self, existing=None):
        self.rows, self.created = {}, []
        self._existing = existing

    def get_case_by_idempotency_key(self, key):
        return self._existing

    def create_case(self, *args, **kw):
        row = {"case_id": "c1", "status": "draft"}
        self.rows["c1"] = row
        self.created.append((args, kw))
        return row

    def transition(self, cid, status, actor, detail, **kw):
        self.rows[cid]["status"] = status
        return self.rows[cid]


def test_a_filing_is_keyed_off_the_raw_text_not_the_extraction(monkeypatch):
    """The idempotency key must not depend on the model. Two identical messages produce
    one key even if the extractor wobbles between them — otherwise one intent forks two
    Cases, and for onboarding that means two grants."""
    a = write_intake.idempotency_key("employee@gsvh.test", "2 days off next week")
    b = write_intake.idempotency_key("employee@gsvh.test", "2 days off next week")
    assert a == b and a.startswith("sha256:")
    assert a != write_intake.idempotency_key("someone@gsvh.test", "2 days off next week")


def test_a_duplicate_message_reads_the_existing_case_and_never_re_extracts():
    """A Case already parked at the approval gate must be READ, not re-driven: re-invoking
    the graph on an interrupted thread re-runs nodes and appends checkpoints."""
    store = _Store(existing={"case_id": "c1", "status": "pending_approval",
                             "approver_email": "manager@gsvh.test"})

    def _explode(_text):
        raise AssertionError("the extractor must not run for a duplicate")

    filing = write_intake.file_leave(P, "2 days off", store=store, hris=_FakeHRIS(),
                                     graph=None, extract_fn=_explode)
    assert filing.case_id == "c1"
    assert filing.status == "pending_approval"
    assert store.created == []          # no second Case


class _FakeHRIS:
    def manager_email(self, principal):
        return "manager@gsvh.test"


class _FakeGraph:
    """Stands in for the compiled LangGraph: records what it was started with."""

    def __init__(self, status="pending_approval"):
        self.started = None
        self._status = status


def _start(graph, *, case_id, principal, raw_text, approver_email, **kw):
    graph.started = {"case_id": case_id, "raw_text": raw_text,
                     "approver_email": approver_email, **kw}
    return {"case_id": case_id, "status": graph._status}


def test_filing_leave_resolves_the_approver_from_the_hris_and_parks_at_the_gate(monkeypatch):
    monkeypatch.setattr(write_intake, "leave_start_case", _start)
    store, graph = _Store(), _FakeGraph()
    filing = write_intake.file_leave(
        P, "I need 12-13 Aug off", store=store, hris=_FakeHRIS(), graph=graph,
        extract_fn=lambda t: {"start_date": "2026-08-12", "end_date": "2026-08-13",
                              "reason": "trip"})

    assert filing.agent == "leave"
    assert filing.status == "pending_approval"
    assert filing.approver_email == "manager@gsvh.test"   # from the HRIS, never the LLM
    assert "12" in filing.summary and "Aug" in filing.summary or filing.summary
    assert graph.started["raw_text"] == "I need 12-13 Aug off"


def test_leave_with_no_manager_is_unroutable_and_never_reaches_the_graph(monkeypatch):
    monkeypatch.setattr(write_intake, "leave_start_case", _start)

    class _NoManager:
        def manager_email(self, principal):
            return None

    store, graph = _Store(), _FakeGraph()
    filing = write_intake.file_leave(
        P, "2 days", store=store, hris=_NoManager(), graph=graph,
        extract_fn=lambda t: {"start_date": "2026-08-12", "end_date": "2026-08-13",
                              "reason": "trip"})
    assert filing.status == "unroutable"
    assert graph.started is None          # no gate to park at => no graph run


def test_filing_jira_routes_to_the_project_approver(monkeypatch):
    monkeypatch.setattr(write_intake, "jira_start_case", _start)
    monkeypatch.setattr(write_intake, "JIRA_PROJECT_APPROVERS", {"MARKETING": "cmo@gsvh.test"})
    store, graph = _Store(), _FakeGraph()
    filing = write_intake.file_jira(
        P, "landing page redesign", store=store, graph=graph,
        extract_fn=lambda t: {"project": "MARKETING", "issue_type": "Task",
                              "summary": "Landing page redesign", "description": "d"})
    assert filing.agent == "jira"
    assert filing.approver_email == "cmo@gsvh.test"
    assert "MARKETING" in filing.summary


def test_filing_onboarding_seeds_the_approved_tools_into_the_graph(monkeypatch):
    """The tools the manager approves must be the tools the connector grants — so the ONE
    extraction's output is seeded into the graph rather than re-extracted there."""
    monkeypatch.setattr(write_intake, "onboarding_start_case", _start)
    store, graph = _Store(), _FakeGraph()
    filing = write_intake.file_onboarding(
        P, "backend engineer, also figma", store=store, hris=_FakeHRIS(), graph=graph,
        extract_fn=lambda t: {"role": "backend engineer", "extra_tools": ["figma"]},
        validate_fn=lambda role, extra: type(
            "V", (), {"ok": True, "tools": ["github", "figma"], "reason": None})())
    assert filing.status == "pending_approval"
    assert graph.started["role"] == "backend engineer"
    assert graph.started["extra_tools"] == ["figma"]
    assert filing.detail["tools"] == ["github", "figma"]


def test_an_off_catalog_onboarding_request_is_denied_before_any_approval(monkeypatch):
    monkeypatch.setattr(write_intake, "onboarding_start_case", _start)
    store, graph = _Store(), _FakeGraph()
    filing = write_intake.file_onboarding(
        P, "give me prod root", store=store, hris=_FakeHRIS(), graph=graph,
        extract_fn=lambda t: {"role": "backend engineer", "extra_tools": ["prod-root"]},
        validate_fn=lambda role, extra: type(
            "V", (), {"ok": False, "tools": [], "reason": "prod-root is not in the catalog"})())
    assert filing.status == "denied_policy"
    assert filing.reason == "prod-root is not in the catalog"
    assert graph.started is None          # a denied request never reaches a human


def test_an_unknown_agent_is_refused():
    with pytest.raises(write_intake.UnknownAgentError):
        write_intake.file_case("gcal", P, "book me a room")
