"""The hybrid policy path fuses a specialist's tool note into the grounded answer;
flag-off / gather failure degrade to pure RAG. chat_service now delegates the gather
to services.supervisor.run_specialist."""
import asyncio

import backend.services.chat_service as cs
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


def test_hybrid_path_folds_tool_note_into_answer(monkeypatch):
    async def fake_run_specialist(specialist, message, history, principal):
        return SpecialistResult(specialist="hr-agent", tool_results=[],
                                tool_note="Live data ... - leave_balance: 12 days remaining.",
                                status="ok")
    monkeypatch.setattr(cs, "run_specialist", fake_run_specialist)

    captured = {}

    def fake_get_llm_response(user_message, context, history, preferences=None,
                              extra_directive=None, temperature=0):
        captured["extra_directive"] = extra_directive
        return "You have 12 leave days remaining. Per the PTO policy, ..."
    monkeypatch.setattr(cs, "get_llm_response", fake_get_llm_response)
    monkeypatch.setattr(cs, "retrieve_context", lambda *a, **k: _Retrieved())
    monkeypatch.setattr(cs, "_resolve_search_query", _async_ret("pto left"))

    result = asyncio.run(cs._answer_policy_query(
        "how many leaves do i have left", [], ["all"], ["global", "us"],
        principal=EMPLOYEE, classification="hr",
    ))
    assert result.status == "ok"
    assert "12" in result.reply
    assert "12" in (captured["extra_directive"] or "")


def test_gather_failure_degrades_to_pure_rag(monkeypatch):
    async def failed(specialist, message, history, principal):
        return SpecialistResult(specialist="hr-agent", status="gather_failed")
    monkeypatch.setattr(cs, "run_specialist", failed)

    captured = {}

    def fake_get_llm_response(user_message, context, history, preferences=None,
                              extra_directive=None, temperature=0):
        captured["extra_directive"] = extra_directive
        return "Full-time employees accrue 20 PTO days."
    monkeypatch.setattr(cs, "get_llm_response", fake_get_llm_response)
    monkeypatch.setattr(cs, "retrieve_context", lambda *a, **k: _Retrieved())
    monkeypatch.setattr(cs, "_resolve_search_query", _async_ret("pto policy"))

    result = asyncio.run(cs._answer_policy_query(
        "how much pto do i get", [], ["all"], ["global", "us"],
        principal=EMPLOYEE, classification="hr",
    ))
    assert result.status == "ok"
    assert "20 PTO days" in result.reply
    assert captured["extra_directive"] is None


def test_policy_agent_no_note_is_pure_rag(monkeypatch):
    # A plain policy query routes to Policy-agent → no_tools → no directive.
    async def no_tools(specialist, message, history, principal):
        return SpecialistResult(specialist="policy-agent", status="no_tools")
    monkeypatch.setattr(cs, "run_specialist", no_tools)

    captured = {}

    def fake_get_llm_response(user_message, context, history, preferences=None,
                              extra_directive=None, temperature=0):
        captured["extra_directive"] = extra_directive
        return "Full-time employees accrue 20 PTO days."
    monkeypatch.setattr(cs, "get_llm_response", fake_get_llm_response)
    monkeypatch.setattr(cs, "retrieve_context", lambda *a, **k: _Retrieved())
    monkeypatch.setattr(cs, "_resolve_search_query", _async_ret("pto policy"))

    result = asyncio.run(cs._answer_policy_query(
        "what is the pto policy", [], ["all"], ["global", "us"],
        principal=EMPLOYEE, classification="policy",
    ))
    assert result.status == "ok"
    assert captured["extra_directive"] is None
