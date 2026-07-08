"""
tests/test_read_pipeline.py
---------------------------
Slice 1 of §6: the shared read pipeline. `prepare_read` runs every shared step
(history, filler guard, classify, build/validate plan, branch, retrieve, gather)
and returns a typed `PreparedRead` that both transports finish. It never calls the
final answer model — that stays transport-specific so streaming can stream.
"""
import asyncio

import backend.services.read_pipeline as rp
from backend.core.control.models import TerminalState
from backend.rag.schema import RetrievedContext


def _run(coro):
    return asyncio.run(coro)


def _prep(monkeypatch, *, history=None, classification="policy"):
    """Common stubs: history load + classifier. Individual tests add retrieval etc."""
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: history or [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: classification)


def test_out_of_scope_is_a_fixed_refusal_no_answer_model(monkeypatch):
    _prep(monkeypatch, classification="out_of_scope")
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")

    prepared = _run(rp.prepare_read("s", "write me an email", owner_user_id=1))

    assert prepared.terminal is TerminalState.REFUSED
    assert prepared.streamable is False
    assert prepared.answer == rp.REFUSAL_MESSAGE
    assert prepared.plan.specialist is None
    assert prepared.plan.allows_answer_model is False
    assert prepared.sources == []


def test_filler_short_circuits_to_clarify_before_classify(monkeypatch):
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")

    def _boom(*a, **k):
        raise AssertionError("classify must not run on filler")
    monkeypatch.setattr(rp, "classify_query", _boom)

    prepared = _run(rp.prepare_read("s", "umm", owner_user_id=1))

    assert prepared.terminal is TerminalState.NO_RESULTS
    assert prepared.answer == rp.CLARIFY_MESSAGE
    assert prepared.streamable is False


def test_policy_branch_is_streamable_and_carries_context(monkeypatch):
    _prep(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context",
                        lambda *a, **k: RetrievedContext("POLICY CTX", sources=[{"source": "hr/x.md"}], status="ok"))

    prepared = _run(rp.prepare_read("s", "what is the PTO policy", allowed_tiers=["all"],
                                    allowed_regions=["global"], owner_user_id=1))

    assert prepared.streamable is True
    assert prepared.answer is None
    assert prepared.terminal is None  # finalize decides after the answer is generated
    assert prepared.context == "POLICY CTX"
    assert prepared.answer_req is not None
    assert prepared.answer_req.context == "POLICY CTX"
    assert prepared.plan.specialist == "policy-agent"


def test_blocked_retrieval_is_terminal_without_answer_model(monkeypatch):
    _prep(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context",
                        lambda *a, **k: RetrievedContext("", status="blocked", blocked_contact="HR"))

    prepared = _run(rp.prepare_read("s", "L5 salary bands", allowed_tiers=["all"],
                                    allowed_regions=["global"], owner_user_id=1))

    assert prepared.terminal is TerminalState.BLOCKED
    assert prepared.streamable is False
    assert "HR" in prepared.answer
    assert prepared.sources == []


def test_no_authorized_context_is_no_results(monkeypatch):
    _prep(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context",
                        lambda *a, **k: RetrievedContext("", sources=[], status="ok"))

    prepared = _run(rp.prepare_read("s", "moon composition", allowed_tiers=["all"],
                                    allowed_regions=["global"], owner_user_id=1))

    assert prepared.terminal is TerminalState.NO_RESULTS
    assert prepared.streamable is False
    assert prepared.answer == rp._localized_no_results("English")


def test_meta_branch_precomputes_answer(monkeypatch):
    _prep(monkeypatch, classification="meta")
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "get_meta_response", lambda *a, **k: "you asked 3 questions")

    prepared = _run(rp.prepare_read("s", "how many questions have i asked", owner_user_id=1))

    assert prepared.terminal is TerminalState.OK
    assert prepared.streamable is False
    assert prepared.answer == "you asked 3 questions"
    assert prepared.plan.specialist is None


# --- Slice 2: shared finalize_answer -------------------------------------------
# The grounded (streamable) branch produces a raw answer string in each transport;
# finalize_answer maps it onto a terminal + delivered answer + envelope sources so
# the two transports can't disagree on grounding.

def _grounded_prepared(sources=None):
    return rp.PreparedRead(
        classification="policy", plan=rp.build_plan("policy"), streamable=True,
        context="CTX", sources=sources if sources is not None else [{"source": "hr/x.md"}],
        language="Hindi",
    )


def test_finalize_grounded_ok_attaches_mapped_sources():
    prepared = _grounded_prepared()
    final = rp.finalize_answer(prepared, "Full-time staff get 20 PTO days.")
    assert final.terminal is TerminalState.OK
    assert final.answer == "Full-time staff get 20 PTO days."
    assert final.sources == rp.to_source_dicts(prepared.sources)


def test_finalize_sentinel_becomes_localized_no_results_no_sources():
    prepared = _grounded_prepared()
    final = rp.finalize_answer(prepared, rp.NO_CONTEXT_SENTINEL)
    assert final.terminal is TerminalState.NO_RESULTS
    assert final.answer == rp._localized_no_results("Hindi")
    assert rp.NO_CONTEXT_SENTINEL not in final.answer
    assert final.sources == []


def test_finalize_refusal_reply_drops_sources():
    prepared = _grounded_prepared()
    final = rp.finalize_answer(prepared, rp.REFUSAL_MESSAGE)
    assert final.terminal is TerminalState.REFUSED
    assert final.sources == []


def test_finalize_insufficient_answer_is_no_results():
    prepared = _grounded_prepared()
    final = rp.finalize_answer(prepared, "The provided documents do not contain that.")
    assert final.terminal is TerminalState.NO_RESULTS
    assert final.sources == []


# --- grounded branch: rephrase directive + specialist tool-note folding --------
# These moved off the retired chat_service._answer_policy_query. prepare_read builds
# the answer_req the transport hands to the model; the tool note and re-explain
# directive are folded there, so both transports carry the same instruction.

from backend.core.agents.specialist import SpecialistResult  # noqa: E402
from backend.core.tools.principal import Principal  # noqa: E402

EMPLOYEE = Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us")


def _grounded_stubs(monkeypatch, classification="policy", sources=None):
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: [])
    monkeypatch.setattr(rp, "classify_query", lambda *a, **k: classification)
    monkeypatch.setattr(rp, "_preferences_note", lambda uid: None)
    monkeypatch.setattr(rp, "_user_language", lambda uid: "English")
    monkeypatch.setattr(rp, "_resolve_search_query",
                        lambda message, history: asyncio.sleep(0, result=message))
    monkeypatch.setattr(rp, "retrieve_context",
                        lambda *a, **k: RetrievedContext(
                            "PTO excerpt.",
                            sources=sources if sources is not None else [{"source": "time-and-leave/pto.md"}],
                            status="ok"))


def _spec_result(**kw):
    async def _fn(specialist, message, history, principal):
        return SpecialistResult(**kw)
    return _fn


def test_grounded_folds_specialist_tool_note_into_directive(monkeypatch):
    _grounded_stubs(monkeypatch, classification="hr")
    monkeypatch.setattr(rp, "run_specialist", _spec_result(
        specialist="hr-agent", tool_results=[],
        tool_note="Live data ... - leave_balance: 12 days remaining.", status="ok"))

    prepared = _run(rp.prepare_read("s", "how many leaves do i have left", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global", "us"], owner_user_id=3))

    assert prepared.streamable is True
    assert "12" in prepared.answer_req.directive


def test_grounded_gather_failure_degrades_to_pure_rag(monkeypatch):
    _grounded_stubs(monkeypatch, classification="hr")
    monkeypatch.setattr(rp, "run_specialist",
                        _spec_result(specialist="hr-agent", status="gather_failed"))

    prepared = _run(rp.prepare_read("s", "how much pto do i get", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global", "us"], owner_user_id=3))

    assert prepared.answer_req.directive is None


def test_rephrase_followup_sets_directive_and_temperature(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "run_specialist",
                        _spec_result(specialist="policy-agent", status="no_tools"))
    history = [{"role": "user", "content": "remote work?"},
               {"role": "assistant", "content": "earlier answer"}]
    monkeypatch.setattr(rp, "_prepare_history", lambda session_id: history)

    prepared = _run(rp.prepare_read("s", "explain it in better terms", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global"], owner_user_id=3))

    assert prepared.answer_req.directive
    assert prepared.answer_req.temperature > 0


def test_first_message_is_not_treated_as_rephrase(monkeypatch):
    _grounded_stubs(monkeypatch, classification="policy")
    monkeypatch.setattr(rp, "run_specialist",
                        _spec_result(specialist="policy-agent", status="no_tools"))

    # "elaborate" with NO prior assistant turn must not trigger re-explain.
    prepared = _run(rp.prepare_read("s", "elaborate", principal=EMPLOYEE,
                                    allowed_tiers=["all"], allowed_regions=["global"], owner_user_id=3))

    assert prepared.answer_req.directive is None
    assert prepared.answer_req.temperature == 0
