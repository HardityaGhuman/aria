"""The chat write lane: a person TALKS to Aria and an agent acts.

This is the one change that touches the SHIPPED read pipeline, so the first property under
test is the one that protects it: with CHAT_WRITE_ENABLED off, nothing about classification
or planning moves. Everything else here is about keeping the LLM out of the control path —
the model names an intent, a FIXED TABLE picks the agent, and the approver check happens on
the server against the Case row, never against what the caller claims to be.
"""
import backend.core.llm as llm
import backend.services.read_planner as planner
from backend.core.control.models import RetrievalRequirement
from backend.services import write_intake


class _Choice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


def _stub_llm(monkeypatch, label: str):
    monkeypatch.setattr(llm, "_invoke",
                        lambda *a, **k: type("R", (), {"choices": [_Choice(label)]})())


# --- the kill switch ------------------------------------------------------------------

def test_with_the_flag_off_a_write_message_classifies_exactly_as_before(monkeypatch):
    """The read pipeline's behaviour is the contract. Off means OFF: the write labels are
    not even offered to the model, and anything that leaks back normalizes to a read lane."""
    monkeypatch.setattr(llm, "CHAT_WRITE_ENABLED", False)
    _stub_llm(monkeypatch, "write_leave")
    assert llm.classify_query("book me 2 days off", []) == "policy"   # fail-toward-grounded


def test_the_write_labels_are_absent_from_the_prompt_when_the_flag_is_off(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "CHAT_WRITE_ENABLED", False)
    monkeypatch.setattr(llm, "_invoke", lambda *a, **k: seen.update(messages=k["messages"])
                        or type("R", (), {"choices": [_Choice("policy")]})())
    llm.classify_query("book me 2 days off", [])
    prompt = " ".join(m["content"] for m in seen["messages"])
    assert "write_leave" not in prompt and "approvals" not in prompt


# --- the lanes ------------------------------------------------------------------------

def test_each_write_intent_gets_its_own_lane(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_WRITE_ENABLED", True)
    for label in ("write_leave", "write_jira", "write_onboarding", "approvals"):
        _stub_llm(monkeypatch, label)
        assert llm.classify_query("...", []) == label


def test_an_unknown_label_still_falls_back_to_policy(monkeypatch):
    monkeypatch.setattr(llm, "CHAT_WRITE_ENABLED", True)
    _stub_llm(monkeypatch, "write_gcal")           # an agent that does not exist
    assert llm.classify_query("book me a meeting room", []) == "policy"


# --- the fixed table ------------------------------------------------------------------

def test_the_write_lanes_have_plans_that_retrieve_nothing_and_call_no_read_tools():
    """A write lane must not smuggle a retrieval or a read tool into its budget: filing a
    Case is not a question, and the plan table is the security boundary."""
    for intent in ("write_leave", "write_jira", "write_onboarding", "approvals"):
        plan = planner.build_plan(intent)
        assert plan.intent == intent
        assert plan.specialist is None
        assert plan.allowed_tools == ()
        assert plan.allows_answer_model is False   # the reply is the Case, not a generation
        assert plan.retrieval is RetrievalRequirement.NONE
        assert plan.max_tool_calls == 0
        assert plan.max_retrieval_calls == 0


def test_the_agent_for_a_lane_is_a_table_not_a_model_choice():
    """The model names the intent; PYTHON picks the agent. A prompt-injected 'write_jira'
    can still only ever reach the jira agent, whose own validator and approval gate remain."""
    assert planner.agent_for_intent("write_leave") == "leave"
    assert planner.agent_for_intent("write_jira") == "jira"
    assert planner.agent_for_intent("write_onboarding") == "onboarding"
    assert planner.agent_for_intent("policy") is None


# --- the pipeline branch --------------------------------------------------------------

import asyncio                                                        # noqa: E402

import backend.services.read_pipeline as pipeline                     # noqa: E402
from backend.core.tools.principal import Principal                    # noqa: E402

P = Principal(user_id=1, email="employee@gsvh.test", role="employee", region="us")


def _prepare(monkeypatch, label, *, enabled=True, **stubs):
    monkeypatch.setattr(pipeline, "CHAT_WRITE_ENABLED", enabled)
    monkeypatch.setattr(pipeline, "classify_query", lambda m, h: label)
    monkeypatch.setattr(pipeline, "_prepare_history_async",
                        lambda sid: asyncio.sleep(0, result=[]))
    for name, value in stubs.items():
        monkeypatch.setattr(pipeline, name, value)
    return asyncio.run(pipeline.prepare_read("s1", "book me 2 days off", principal=P,
                                             owner_user_id=1))


def test_a_write_lane_files_a_case_and_answers_with_the_card(monkeypatch):
    filed = write_intake.Filing(agent="leave", case_id="c1", status="pending_approval",
                                approver_email="manager@gsvh.test",
                                summary="2026-08-12 to 2026-08-13 · 2 day(s)")
    monkeypatch.setattr(pipeline, "file_case", lambda agent, principal, text: filed)
    prepared = _prepare(monkeypatch, "write_leave", agent_available=lambda a: True)

    assert prepared.classification == "write_leave"
    assert prepared.streamable is False              # nothing to generate; the Case IS the reply
    assert len(prepared.cases) == 1
    card = prepared.cases[0]
    assert card["case_id"] == "c1" and card["agent"] == "leave"
    assert card["status"] == "pending_approval"
    assert "manager@gsvh.test" in prepared.answer     # the human is told who must decide


def test_a_write_lane_with_the_agent_switched_off_degrades_to_a_normal_read(monkeypatch):
    """Fail-safe: an agent that is not registered (kill switch off, or not built yet) must
    not error the chat. The lane normalizes back to `policy` BEFORE the plan is built, so
    the turn takes the ordinary grounded path — exactly today's behaviour."""
    monkeypatch.setattr(pipeline, "CHAT_WRITE_ENABLED", True)
    monkeypatch.setattr(pipeline, "agent_available", lambda agent: False)
    assert pipeline._write_lane_or_read("write_leave") == "policy"
    assert pipeline._write_lane_or_read("approvals") == "approvals"   # needs no agent

    monkeypatch.setattr(pipeline, "agent_available", lambda agent: True)
    assert pipeline._write_lane_or_read("write_leave") == "write_leave"


def test_with_the_flag_off_no_write_lane_survives_into_the_plan(monkeypatch):
    monkeypatch.setattr(pipeline, "CHAT_WRITE_ENABLED", False)
    monkeypatch.setattr(pipeline, "agent_available", lambda agent: True)
    assert pipeline._write_lane_or_read("write_leave") == "policy"
    assert pipeline._write_lane_or_read("approvals") == "policy"


def test_the_approvals_lane_lists_only_cases_awaiting_this_caller(monkeypatch):
    rows = [{"case_id": "c9", "agent": "jira", "status": "pending_approval",
             "employee_email": "alice@gsvh.test", "approver_email": "employee@gsvh.test",
             "project": "MARKETING", "summary": "Landing page"}]
    monkeypatch.setattr(pipeline, "list_cases_for_approver", lambda email: rows)
    prepared = _prepare(monkeypatch, "approvals")

    assert prepared.classification == "approvals"
    assert [c["case_id"] for c in prepared.cases] == ["c9"]
    assert prepared.cases[0]["can_decide"] is True   # the caller IS the approver


def test_an_empty_approval_inbox_says_so_rather_than_rendering_nothing(monkeypatch):
    monkeypatch.setattr(pipeline, "list_cases_for_approver", lambda email: [])
    prepared = _prepare(monkeypatch, "approvals")
    assert prepared.cases == []
    assert "nothing" in prepared.answer.lower() or "no " in prepared.answer.lower()
