"""The supervisor routes a classification to exactly one specialist and runs it
over its SCOPED registry. Read tool-select runs on the 20b model; flag-off / no
tools / gather failure all degrade to a no-note result (pure RAG upstream)."""
import asyncio

import backend.services.supervisor as sup
from backend.core.agents.build import build_specialists
from backend.core.agents.specialist import Specialist
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry
from backend.services.agent_loop import AgentOutcome

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def _run(coro):
    return asyncio.run(coro)


def test_route_hr_to_hr_agent():
    assert sup.route("hr", EMPLOYEE).name == "hr-agent"


def test_route_policy_to_policy_agent():
    assert sup.route("policy", EMPLOYEE).name == "policy-agent"


def test_route_unmapped_defaults_to_policy():
    assert sup.route("meta", EMPLOYEE).name == "policy-agent"


def test_route_calendar_to_calendar_agent():
    # §5.3: the interim hr-agent route is retired — a calendar-labeled query now
    # reaches the dedicated calendar-agent (whos_out lives there, not on hr-agent).
    assert sup.route("calendar", EMPLOYEE).name == "calendar-agent"


def test_route_rbac_fallback_to_policy():
    # A specialist gated above the caller's role falls back to Policy-agent — never
    # exposes a specialist the role can't reach.
    gated = Specialist(name="hr-agent", description="", min_role="hr",
                       registry=ToolRegistry(), uses_tools=True)
    policy = Specialist(name="policy-agent", description="", min_role="employee",
                        registry=ToolRegistry(), uses_tools=False)
    chosen = sup.route("hr", EMPLOYEE, specialists=[gated, policy])
    assert chosen.name == "policy-agent"


def test_build_tool_note_from_typed_fields():
    results = [{"name": "leave_balance", "result": ToolResult(status="ok",
               data={"remaining": 12, "total": 20, "used": 8}, summary="12 leave days remaining.")}]
    note = sup.build_tool_note(results)
    assert note is not None and "12" in note and "leave_balance" in note
    assert sup.build_tool_note([]) is None


def test_run_specialist_policy_is_no_tools(monkeypatch):
    monkeypatch.setattr(sup, "AGENT_TOOLS_ENABLED", True)
    policy = next(s for s in build_specialists() if s.name == "policy-agent")
    out = _run(sup.run_specialist(policy, "q", [], EMPLOYEE))
    assert out.status == "no_tools"
    assert out.tool_note is None


def test_run_specialist_flag_off_is_no_tools(monkeypatch):
    monkeypatch.setattr(sup, "AGENT_TOOLS_ENABLED", False)

    def boom(*a, **k):
        raise AssertionError("loop must not run when flag off")
    monkeypatch.setattr(sup, "run_agent_loop", boom)
    hr = next(s for s in build_specialists() if s.name == "hr-agent")
    out = _run(sup.run_specialist(hr, "leaves?", [], EMPLOYEE))
    assert out.status == "no_tools"
    assert out.tool_note is None


def test_run_specialist_hr_gathers_and_builds_note(monkeypatch):
    monkeypatch.setattr(sup, "AGENT_TOOLS_ENABLED", True)

    def fake_loop(message, history, principal, registry, **kwargs):
        assert kwargs.get("gather_only") is True
        return AgentOutcome(status="gathered", answer=None, tool_results=[
            {"name": "leave_balance", "result": ToolResult(status="ok",
             data={"remaining": 12, "total": 20, "used": 8}, summary="12 leave days remaining.")}])
    monkeypatch.setattr(sup, "run_agent_loop", fake_loop)
    hr = next(s for s in build_specialists() if s.name == "hr-agent")
    out = _run(sup.run_specialist(hr, "leaves?", [], EMPLOYEE))
    assert out.status == "ok"
    assert "12" in out.tool_note


def test_run_specialist_gather_failure_degrades(monkeypatch):
    monkeypatch.setattr(sup, "AGENT_TOOLS_ENABLED", True)

    def exploding(*a, **k):
        raise RuntimeError("select provider down")
    monkeypatch.setattr(sup, "run_agent_loop", exploding)
    hr = next(s for s in build_specialists() if s.name == "hr-agent")
    out = _run(sup.run_specialist(hr, "leaves?", [], EMPLOYEE))
    assert out.status == "gather_failed"
    assert out.tool_note is None


def test_run_specialist_uses_20b_read_model(monkeypatch):
    monkeypatch.setattr(sup, "AGENT_TOOLS_ENABLED", True)
    from backend.core.config import ROUTER_MODEL_NAME
    from backend.core.llm import ToolSelection

    seen = {}

    def spy_select(user_message, tool_specs, history=None, model=None, tool_choice="auto"):
        seen["model"] = model
        return ToolSelection(calls=[], text=None)
    monkeypatch.setattr(sup, "select_tool_call", spy_select)
    hr = next(s for s in build_specialists() if s.name == "hr-agent")
    _run(sup.run_specialist(hr, "leaves?", [], EMPLOYEE))
    assert seen["model"] == ROUTER_MODEL_NAME
