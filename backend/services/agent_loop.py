"""services/agent_loop.py
------------------------
The bounded, hand-rolled agent loop (no LangGraph — see the model-allocation spec
§3). One supervisor model selects a tool per step; the loop enforces the security
invariants that make a tool call safe:

  * identity — tools act on the Principal only (enforced in the registry);
  * validate-or-repair — a schema-invalid call gets ONE repair re-ask, then errors,
    and is never executed;
  * confirmation gate — a write tool never runs on first sight; it returns
    confirmation_required and executes only when the caller passes confirmed=True
    (a flag set by the user's explicit next-turn "yes", never by LLM/doc/history text);
  * loop cap — at most MAX_TOOL_STEPS iterations bound cost/latency.

Sync by design: chat_service offloads the whole loop via asyncio.to_thread, matching
how the other blocking LLM calls are run."""
from dataclasses import dataclass, field

from backend.core.config import MAX_TOOL_STEPS
from backend.core.tools.base import validate_args
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry


@dataclass
class AgentOutcome:
    # answer | confirmation_required | tool_error | loop_exhausted
    status: str
    answer: str | None = None
    summary: str | None = None
    pending: dict | None = None
    tool_results: list[dict] = field(default_factory=list)


def _default_select_fn():
    # Imported lazily so tests can run the loop with an injected fake without
    # importing litellm, and to keep the import graph shallow.
    from backend.core.llm import select_tool_call
    return select_tool_call


def run_agent_loop(
    message: str,
    history: list[dict],
    principal: Principal,
    registry: ToolRegistry,
    *,
    max_steps: int | None = None,
    confirmed: bool = False,
    select_fn=None,
) -> AgentOutcome:
    max_steps = MAX_TOOL_STEPS if max_steps is None else max_steps
    select_fn = select_fn or _default_select_fn()
    working_message = message
    tool_results: list[dict] = []

    for _ in range(max_steps):
        specs = registry.specs_for(principal)
        selection = select_fn(working_message, specs, history)

        if not selection.calls:
            return AgentOutcome(status="answer", answer=selection.text, tool_results=tool_results)

        call = selection.calls[0]
        tool = registry.get(call["name"])
        if tool is None:
            return AgentOutcome(status="tool_error", answer=f"unknown tool '{call['name']}'",
                                tool_results=tool_results)

        # validate-or-repair: one bounded re-ask on invalid args, then hard error.
        errors = validate_args(tool.parameters, call.get("args", {}))
        if errors:
            hint = (
                f"{working_message}\n\n[your previous tool call was invalid: "
                f"{'; '.join(errors)}. Re-emit a valid call.]"
            )
            selection = select_fn(hint, specs, history)
            if not selection.calls:
                return AgentOutcome(status="answer", answer=selection.text, tool_results=tool_results)
            call = selection.calls[0]
            tool = registry.get(call["name"]) or tool
            errors = validate_args(tool.parameters, call.get("args", {}))
            if errors:
                return AgentOutcome(status="tool_error",
                                    answer="I couldn't build a valid request for that.",
                                    tool_results=tool_results)

        # confirmation gate — a write never runs without explicit confirmation.
        if tool.requires_confirmation and not confirmed:
            summary = getattr(tool, "confirm_summary", None)
            return AgentOutcome(
                status="confirmation_required",
                summary=summary(call["args"]) if callable(summary) else f"Confirm: {call['name']} {call['args']}",
                pending={"name": call["name"], "args": call["args"]},
                tool_results=tool_results,
            )

        result = registry.invoke(call["name"], call["args"], principal)
        tool_results.append({"name": call["name"], "result": result})
        # Feed the tool output back so the next step can answer or chain.
        working_message = (
            f"{message}\n\n[tool '{call['name']}' returned: {result.summary or result.data or result.error}]"
        )

    return AgentOutcome(status="loop_exhausted",
                        answer="I couldn't complete that within the step limit.",
                        tool_results=tool_results)
