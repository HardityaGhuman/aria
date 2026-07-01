"""The streaming policy path emits the reserved tool_call/tool_result SSE events
when a tool runs, and folds the live number into the streamed grounded answer."""
import asyncio

import backend.services.chat_service as cs
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.services.agent_loop import AgentOutcome

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def _collect(agen):
    async def _run():
        return [e async for e in agen]
    return asyncio.run(_run())


def test_stream_emits_tool_events_and_fuses_number(monkeypatch):
    monkeypatch.setattr(cs, "AGENT_TOOLS_ENABLED", True)
    monkeypatch.setattr(cs, "_prepare_history", lambda sid: [])
    monkeypatch.setattr(cs, "classify_query", lambda *a, **k: "policy")
    monkeypatch.setattr(cs, "_resolve_search_query", _async_ret("pto left"))
    monkeypatch.setattr(cs, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(cs, "_user_language", lambda uid: "English")

    class _Ret:
        status = "ok"; text = "PTO policy excerpt."; sources = [{"source": "time-and-leave/working-hours-and-pto.md", "access_tier": "all"}]
        blocked_contact = None
    monkeypatch.setattr(cs, "retrieve_context", lambda *a, **k: _Ret())

    def fake_loop(message, history, principal, registry, **kwargs):
        return AgentOutcome(status="gathered", answer=None, tool_results=[
            {"name": "leave_balance", "result": ToolResult(status="ok",
             data={"remaining": 12}, summary="12 leave days remaining.")}])
    monkeypatch.setattr(cs, "run_agent_loop", fake_loop)
    monkeypatch.setattr(cs, "stream_llm_response",
                        lambda *a, **k: iter(["You have ", "12 ", "days."]))
    monkeypatch.setattr(cs, "_persist_quietly", _async_none)

    events = _collect(cs.stream_chat_reply("s1", "leaves left?", ["all"], ["global", "us"],
                                           owner_user_id=3, principal=EMPLOYEE))
    kinds = [e["event"] for e in events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    # tool events precede the first token
    assert kinds.index("tool_call") < kinds.index("token")
    answer = "".join(e["data"]["delta"] for e in events if e["event"] == "token")
    assert "12" in answer


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn


async def _async_none(*a, **k):
    return None
