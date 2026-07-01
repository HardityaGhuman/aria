"""select_tool_call turns a native function-calling response into a structured
ToolSelection. It parses tool_calls; malformed JSON args degrade to {} (the loop's
validator then rejects them) rather than crashing."""
import json
from types import SimpleNamespace

import backend.core.llm as llm


def _response_with_tool_call(name, args_json):
    fn = SimpleNamespace(name=name, arguments=args_json)
    call = SimpleNamespace(function=fn, id="call_1")
    message = SimpleNamespace(content=None, tool_calls=[call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _response_plain_text(text):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_parses_a_tool_call(monkeypatch):
    monkeypatch.setattr(
        llm, "_invoke",
        lambda *a, **k: _response_with_tool_call("echo", json.dumps({"text": "hi"})),
    )
    sel = llm.select_tool_call("say hi", [{"type": "function", "function": {"name": "echo"}}])
    assert sel.calls == [{"name": "echo", "args": {"text": "hi"}}]
    assert sel.text is None


def test_plain_text_answer_has_no_calls(monkeypatch):
    monkeypatch.setattr(llm, "_invoke", lambda *a, **k: _response_plain_text("Here is your answer."))
    sel = llm.select_tool_call("hello", [])
    assert sel.calls == []
    assert sel.text == "Here is your answer."


def test_malformed_arguments_degrade_to_empty_dict(monkeypatch):
    monkeypatch.setattr(
        llm, "_invoke",
        lambda *a, **k: _response_with_tool_call("echo", "{not valid json"),
    )
    sel = llm.select_tool_call("say hi", [])
    assert sel.calls == [{"name": "echo", "args": {}}]


def test_uses_main_model_by_default(monkeypatch):
    captured = {}

    def fake_invoke(purpose, model, **kwargs):
        captured["model"] = model
        captured["tools"] = kwargs.get("tools")
        return _response_plain_text("ok")

    monkeypatch.setattr(llm, "_invoke", fake_invoke)
    llm.select_tool_call("q", [{"type": "function", "function": {"name": "echo"}}])
    assert captured["model"] == llm.MODEL_NAME
    assert captured["tools"] is not None
