"""gather_only mode: the loop collects read-tool results and returns them WITHOUT
generating a final prose answer, so the caller can fuse them via the grounded
answer model. Default (gather_only=False) preserves Unit 1 behavior."""
from backend.core.llm import ToolSelection
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry
from backend.core.tools.stub import EchoTool
from backend.services.agent_loop import run_agent_loop

EMPLOYEE = Principal(user_id=1, email="e@x.test", role="employee", region="us")


def _registry():
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _scripted(script):
    calls = list(script)

    def fake(message, specs, history):
        return calls.pop(0)
    return fake


def test_gather_only_returns_gathered_with_results_no_answer():
    fn = _scripted([
        ToolSelection(calls=[{"name": "echo", "args": {"text": "hi"}}], text=None),
        ToolSelection(calls=[], text="ignored prose"),
    ])
    out = run_agent_loop("q", [], EMPLOYEE, _registry(), select_fn=fn, gather_only=True)
    assert out.status == "gathered"
    assert out.answer is None
    assert out.tool_results[0]["result"].data["echo"] == "hi"


def test_gather_only_with_no_tool_call_returns_empty_gathered():
    fn = _scripted([ToolSelection(calls=[], text="just answer")])
    out = run_agent_loop("q", [], EMPLOYEE, _registry(), select_fn=fn, gather_only=True)
    assert out.status == "gathered"
    assert out.tool_results == []


def test_default_mode_still_returns_answer():
    # Regression: Unit 1 behavior preserved when gather_only is not set.
    fn = _scripted([ToolSelection(calls=[], text="the answer")])
    out = run_agent_loop("q", [], EMPLOYEE, _registry(), select_fn=fn)
    assert out.status == "answer"
    assert out.answer == "the answer"
