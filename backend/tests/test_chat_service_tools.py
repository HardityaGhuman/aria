"""The hybrid policy path: when tools are enabled, a leave-balance question fuses
the live HRIS number into the grounded answer; when the flag is off, the loop is
never touched (behavioral flag-off regression)."""
import backend.services.chat_service as cs
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def test_flag_off_never_enters_loop(monkeypatch):
    monkeypatch.setattr(cs, "AGENT_TOOLS_ENABLED", False)

    def boom(*a, **k):
        raise AssertionError("agent loop must not run when flag is off")

    monkeypatch.setattr(cs, "run_agent_loop", boom)
    directive = cs._tool_results_directive([])
    assert directive is None  # nothing gathered → no note


def test_tool_results_directive_carries_live_number():
    from backend.core.tools.base import ToolResult
    results = [{"name": "leave_balance", "result": ToolResult(status="ok",
               data={"remaining": 12, "total": 20, "used": 8},
               summary="12 leave days remaining (20 total, 8 used).")}]
    note = cs._tool_results_directive(results)
    assert note is not None
    assert "12" in note
    # Framed as trusted live data (distinct from untrusted retrieved doc text).
    assert "leave_balance" in note


def test_hybrid_path_folds_tool_note_into_answer(monkeypatch):
    monkeypatch.setattr(cs, "AGENT_TOOLS_ENABLED", True)

    # Fake gather: the loop "ran" leave_balance and returned a live number.
    from backend.core.tools.base import ToolResult
    from backend.services.agent_loop import AgentOutcome

    def fake_loop(message, history, principal, registry, **kwargs):
        assert kwargs.get("gather_only") is True
        return AgentOutcome(status="gathered", answer=None, tool_results=[
            {"name": "leave_balance", "result": ToolResult(status="ok",
             data={"remaining": 12}, summary="12 leave days remaining.")}])

    captured = {}

    def fake_get_llm_response(user_message, context, history, preferences=None,
                              extra_directive=None, temperature=0):
        captured["extra_directive"] = extra_directive
        return "You have 12 leave days remaining. Per the PTO policy, ..."

    monkeypatch.setattr(cs, "run_agent_loop", fake_loop)
    monkeypatch.setattr(cs, "get_llm_response", fake_get_llm_response)

    # Retrieval returns some policy context + a source.
    class _Ret:
        status = "ok"; text = "PTO policy excerpt."; sources = [{"source": "time-and-leave/working-hours-and-pto.md"}]
        blocked_contact = None
    monkeypatch.setattr(cs, "retrieve_context", lambda *a, **k: _Ret())
    monkeypatch.setattr(cs, "_resolve_search_query", _async_ret("pto left"))

    import asyncio
    result = asyncio.run(cs._answer_policy_query(
        "how many leaves do i have left", [], ["all"], ["global", "us"],
        principal=EMPLOYEE,
    ))
    assert result.status == "ok"
    assert "12" in result.reply
    assert "12" in (captured["extra_directive"] or "")  # the live note reached the model


def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn
