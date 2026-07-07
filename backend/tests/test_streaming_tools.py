"""The streaming policy path emits the reserved tool_call/tool_result SSE events
when a specialist gathered a tool, and folds the live number into the streamed
grounded answer."""
import asyncio

import backend.services.chat_service as cs
import backend.services.read_pipeline as rp
from backend.core.agents.specialist import SpecialistResult
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def _collect(agen):
    async def _run():
        return [e async for e in agen]
    return asyncio.run(_run())


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn


async def _async_none(*a, **k):
    return None


def test_stream_emits_tool_events_and_fuses_number(monkeypatch):
    # Pipeline deps patched on rp; the answer-model stream + persistence stay on cs.
    monkeypatch.setattr(rp, "_prepare_history", lambda sid: [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: "hr")
    monkeypatch.setattr(rp, "_resolve_search_query", _async_ret("pto left"))
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")

    class _Ret:
        status = "ok"; text = "PTO policy excerpt."; sources = [{"source": "time-and-leave/working-hours-and-pto.md", "access_tier": "all"}]
        blocked_contact = None
    monkeypatch.setattr(rp, "retrieve_context", lambda *a, **k: _Ret())

    async def fake_run_specialist(specialist, message, history, principal):
        return SpecialistResult(specialist="hr-agent", tool_results=[
            {"name": "leave_balance", "result": ToolResult(status="ok",
             data={"remaining": 12}, summary="12 leave days remaining.")}],
            tool_note="Live data ... - leave_balance: 12 leave days remaining.", status="ok")
    monkeypatch.setattr(rp, "run_specialist", fake_run_specialist)
    monkeypatch.setattr(cs, "stream_llm_response",
                        lambda *a, **k: iter(["You have ", "12 ", "days."]))
    monkeypatch.setattr(cs, "_persist_quietly", _async_none)

    events = _collect(cs.stream_chat_reply("s1", "leaves left?", ["all"], ["global", "us"],
                                           owner_user_id=3, principal=EMPLOYEE))
    kinds = [e["event"] for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert kinds.index("tool_call") < kinds.index("token")
    answer = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert "12" in answer
