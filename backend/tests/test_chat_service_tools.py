"""The hybrid policy path fuses a specialist's tool note into the grounded answer;
flag-off / gather failure degrade to pure RAG. The gather + note-folding now live in
the shared read pipeline (prepare_read), which builds the answer_req.directive the
transport hands to the answer model."""
import asyncio

import backend.services.read_pipeline as rp
from backend.core.agents.specialist import SpecialistResult
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn


class _Retrieved:
    status = "ok"
    text = "PTO policy excerpt."
    sources = [{"source": "time-and-leave/working-hours-and-pto.md"}]
    blocked_contact = None


def _stubs(monkeypatch, classification):
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: classification)
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "retrieve_context", lambda *a, **k: _Retrieved())
    monkeypatch.setattr(rp, "_resolve_search_query", _async_ret("q"))


def _prepare(classification):
    return asyncio.run(rp.prepare_read(
        "s", "how many leaves do i have left", principal=EMPLOYEE,
        allowed_tiers=["all"], allowed_regions=["global", "us"], owner_user_id=3))


def test_hybrid_path_folds_tool_note_into_directive(monkeypatch):
    _stubs(monkeypatch, "hr")

    async def fake_run_specialist(specialist, message, history, principal):
        return SpecialistResult(specialist="hr-agent", tool_results=[],
                                tool_note="Live data ... - leave_balance: 12 days remaining.",
                                status="ok")
    monkeypatch.setattr(rp, "run_specialist", fake_run_specialist)

    prepared = _prepare("hr")
    assert prepared.streamable is True
    assert "12" in prepared.answer_req.directive


def test_gather_failure_degrades_to_pure_rag(monkeypatch):
    _stubs(monkeypatch, "hr")

    async def failed(specialist, message, history, principal):
        return SpecialistResult(specialist="hr-agent", status="gather_failed")
    monkeypatch.setattr(rp, "run_specialist", failed)

    prepared = _prepare("hr")
    assert prepared.streamable is True
    assert prepared.answer_req.directive is None


def test_policy_agent_no_note_is_pure_rag(monkeypatch):
    # A plain policy query routes to Policy-agent → no_tools → no directive.
    _stubs(monkeypatch, "policy")

    async def no_tools(specialist, message, history, principal):
        return SpecialistResult(specialist="policy-agent", status="no_tools")
    monkeypatch.setattr(rp, "run_specialist", no_tools)

    prepared = _prepare("policy")
    assert prepared.streamable is True
    assert prepared.answer_req.directive is None
