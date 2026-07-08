"""
tests/test_read_pipeline_control.py
-----------------------------------
§6a/6b/6c: the control layer goes LIVE inside the shared pipeline.

6a — `validate_plan` + `BoundaryTracer` wired into `prepare_read`: an off-table /
     unreachable plan terminates in `invalid_plan` before any answer model, and the
     boundary events are emitted through the telemetry sink. The transports turn an
     error-terminal into an `AppError` / SSE `error` (it can't ride the ChatResponse
     status Literal).
6b — HR specialist: a gather failure with policy evidence degrades to `partial`
     (never a silent `ok`); a numerically bad leave result never asserts a balance.
6c — policy citations: a malformed source (empty document_id) is dropped before an
     `ok` completion.
"""
import asyncio

import pytest

import backend.services.chat_service as cs
import backend.services.read_pipeline as rp
import backend.services.supervisor as sup
from backend.core.agents.specialist import SpecialistResult
from backend.core.control.models import (
    RetrievalRequirement,
    ReadPlan,
    TerminalState,
)
from backend.core.errors import AppError
from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.rag.schema import RetrievedContext
from backend.services.read_planner import _ANSWER_TIMEOUT_MS

EMPLOYEE = Principal(user_id=3, email="e@x.test", role="employee", region="us")


def _run(coro):
    return asyncio.run(coro)


def _off_table_plan() -> ReadPlan:
    """A registry-valid but table-illegal plan: intent=policy routed to the hr-agent
    with a live tool. validate_plan must reject it as plan_off_table."""
    return ReadPlan(
        intent="policy", specialist="hr-agent", allowed_tools=("leave_balance",),
        retrieval=RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=1, max_retrieval_calls=1, allows_answer_model=True,
        timeout_ms=_ANSWER_TIMEOUT_MS,
    )


def _grounded_stubs(monkeypatch, classification="policy", retrieved=None,
                    spec=SpecialistResult(specialist="policy-agent", status="no_tools")):
    monkeypatch.setattr(rp, "_prepare_history", lambda sid: [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: classification)
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context", lambda *a, **k: retrieved or RetrievedContext(
        "PTO excerpt.", sources=[{"source": "time-and-leave/pto.md"}], status="ok"))

    async def _spec(specialist, message, history, principal):
        return spec
    monkeypatch.setattr(rp, "run_specialist", _spec)


# --- 6a: list_specialists -----------------------------------------------------

def test_list_specialists_returns_the_three_isolated_specialists():
    names = {s.name for s in sup.list_specialists()}
    assert names == {"policy-agent", "hr-agent", "calendar-agent"}


# --- 6a: invalid plan terminates before the answer model ----------------------

def test_invalid_plan_terminates_as_invalid_plan(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "build_plan", lambda intent: _off_table_plan())

    prepared = _run(rp.prepare_read("s", "what is the PTO policy", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global"], owner_user_id=3))

    assert prepared.terminal is TerminalState.INVALID_PLAN
    assert prepared.streamable is False
    assert prepared.answer is None


def test_valid_plan_still_streams(monkeypatch):
    # With a real (table) plan and a reachable principal, validation passes and the
    # grounded branch is unaffected.
    _grounded_stubs(monkeypatch, classification="policy")
    prepared = _run(rp.prepare_read("s", "pto policy", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global"], owner_user_id=3))
    assert prepared.streamable is True


def test_validation_skipped_without_principal(monkeypatch):
    # No principal (unauthenticated internal call): validate_plan is not run, so even
    # a normally-invalid config can't be exercised — the branch simply proceeds.
    _grounded_stubs(monkeypatch, classification="policy")
    prepared = _run(rp.prepare_read("s", "pto policy", allowed_tiers=["all"],
                                    allowed_regions=["global"], owner_user_id=3))
    assert prepared.streamable is True


# --- 6a: transports turn an error-terminal into AppError / SSE error -----------

def _cs_stubs(monkeypatch):
    monkeypatch.setattr(cs, "append_exchange", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_persist_quietly", lambda *a, **k: asyncio.sleep(0))


def test_sync_transport_raises_on_invalid_plan(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "build_plan", lambda intent: _off_table_plan())
    _cs_stubs(monkeypatch)

    with pytest.raises(AppError):
        _run(cs.generate_chat_reply("s", "pto policy", ["all"], ["global"],
                                    owner_user_id=3, principal=EMPLOYEE))


def test_stream_transport_emits_error_on_invalid_plan(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "build_plan", lambda intent: _off_table_plan())
    _cs_stubs(monkeypatch)

    async def _collect():
        return [e async for e in cs.stream_chat_reply(
            "s", "pto policy", ["all"], ["global"], owner_user_id=3, principal=EMPLOYEE)]
    events = _run(_collect())
    assert any(e["event"] == "error" for e in events)
    assert not any(e["event"] == "done" for e in events)


# --- 6a: boundary events are emitted ------------------------------------------

def test_prepare_read_emits_core_boundary_events(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    events = []
    monkeypatch.setattr("backend.core.trace._emit", lambda record: events.append(record["event"]))

    _run(rp.prepare_read("s", "pto policy", principal=EMPLOYEE,
                         allowed_tiers=["all"], allowed_regions=["global"], owner_user_id=3))

    for name in ("request_received", "intent_classified", "plan_built",
                 "plan_validated", "specialist_selected",
                 "retrieval_started", "retrieval_completed"):
        assert name in events, f"missing boundary event {name!r}: {events}"


# --- 6b: HR gather failure with policy evidence degrades to partial ------------

def test_hr_gather_failure_with_policy_is_partial(monkeypatch):
    _grounded_stubs(monkeypatch, classification="hr",
                    spec=SpecialistResult(specialist="hr-agent", status="gather_failed"))
    prepared = _run(rp.prepare_read("s", "how many leaves do i have", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global", "us"], owner_user_id=3))
    # Still streamable (answers from policy), but finalize must settle to PARTIAL.
    assert prepared.streamable is True
    assert prepared.forced_partial is True
    final = rp.finalize_answer(prepared, "Full-time staff accrue 20 PTO days.")
    assert final.terminal is TerminalState.PARTIAL
    assert final.sources  # policy evidence is cited on the partial answer


def test_hr_ok_is_not_forced_partial(monkeypatch):
    _grounded_stubs(monkeypatch, classification="hr",
                    spec=SpecialistResult(specialist="hr-agent",
                                          tool_note="Live data - leave_balance: 12 remaining.", status="ok"))
    prepared = _run(rp.prepare_read("s", "leaves left", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global", "us"], owner_user_id=3))
    assert prepared.forced_partial is False


# --- 6b: numerically bad leave result never asserts a balance ------------------

def test_bad_leave_numbers_are_not_folded_into_note():
    bad = [{"name": "leave_balance",
            "result": ToolResult(status="ok", data={"total": -5, "used": 2, "remaining": -7},
                                 summary="-7 leave days remaining (-5 total, 2 used).")}]
    assert sup.build_tool_note(bad) is None


def test_good_leave_numbers_are_folded_into_note():
    good = [{"name": "leave_balance",
             "result": ToolResult(status="ok", data={"total": 20, "used": 8, "remaining": 12},
                                  summary="12 leave days remaining (20 total, 8 used).")}]
    note = sup.build_tool_note(good)
    assert note and "12 leave days remaining" in note


def test_no_record_leave_result_is_still_note_worthy():
    no_rec = [{"name": "leave_balance",
               "result": ToolResult(status="ok", data={"remaining": None},
                                    summary="No HRIS leave record found for this employee.")}]
    note = sup.build_tool_note(no_rec)
    assert note and "No HRIS leave record" in note


# --- 6c: malformed citation dropped before an ok completion -------------------

def test_malformed_source_is_dropped():
    mapped = rp.to_source_dicts([
        {"source": "hr/x.md", "section": "A", "access_tier": "all"},
        {"source": "", "section": "B", "access_tier": "all"},  # empty document_id
    ])
    assert [m["document_id"] for m in mapped] == ["hr/x.md"]
