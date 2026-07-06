"""§4.2 — the deterministic plan table. `build_plan` turns a classifier intent into
a typed `ReadPlan` in pure Python (no LLM), and `validate_plan` rejects any plan
whose specialist/tools/budgets/Principal don't line up BEFORE anything executes.
Every intent→plan mapping and every validation rejection is pinned here."""
import pytest

from backend.core.control import models as m
from backend.core.tools.principal import Principal
from backend.core.tools.registry import ToolRegistry
from backend.core.tools.leave_balance import LeaveBalanceTool
from backend.core.tools.whos_out import WhosOutTool
from backend.core.hris.mock import MockHRIS
from backend.core.calendar.mock import MockCalendar
from backend.core.agents.specialist import Specialist
from backend.services import read_planner as rp


def _principal(role="employee") -> Principal:
    return Principal(user_id=7, email="e@x.test", role=role, region="us")


def _pool() -> list[Specialist]:
    """A specialist pool mirroring the post-§5 wiring so validate_plan has each
    plan's target specialist to check against."""
    hr = ToolRegistry(); hr.register(LeaveBalanceTool(MockHRIS()))
    cal = ToolRegistry(); cal.register(WhosOutTool(MockCalendar()))
    return [
        Specialist("hr-agent", "hr", "employee", hr, uses_tools=True),
        Specialist("policy-agent", "policy", "employee", ToolRegistry(), uses_tools=False),
        Specialist("calendar-agent", "cal", "employee", cal, uses_tools=True),
    ]


# --- build_plan: the fixed table (canonical §7) ---------------------------

def test_policy_plan():
    p = rp.build_plan("policy")
    assert p.specialist == "policy-agent"
    assert p.allowed_tools == ()
    assert p.retrieval is m.RetrievalRequirement.REQUIRED
    assert p.needs_live_data is False
    assert (p.max_tool_calls, p.max_retrieval_calls) == (0, 1)
    assert p.allows_answer_model is True


def test_hr_plan():
    p = rp.build_plan("hr")
    assert p.specialist == "hr-agent"
    assert p.allowed_tools == ("leave_balance",)
    assert p.retrieval is m.RetrievalRequirement.REQUIRED
    assert p.needs_live_data is True
    assert (p.max_tool_calls, p.max_retrieval_calls) == (1, 1)
    assert p.allows_answer_model is True


def test_calendar_plan():
    p = rp.build_plan("calendar")
    assert p.specialist == "calendar-agent"
    assert p.allowed_tools == ("whos_out",)
    assert p.retrieval is m.RetrievalRequirement.OPTIONAL
    assert p.needs_live_data is True
    assert p.max_tool_calls == 1
    assert p.allows_answer_model is True


@pytest.mark.parametrize("intent", ["meta", "chitchat"])
def test_conversational_plans_have_no_specialist_or_tools(intent):
    p = rp.build_plan(intent)
    assert p.specialist is None
    assert p.allowed_tools == ()
    assert p.retrieval is m.RetrievalRequirement.NONE
    assert (p.max_tool_calls, p.max_retrieval_calls) == (0, 0)
    assert p.allows_answer_model is True


def test_out_of_scope_plan_forbids_answer_model():
    p = rp.build_plan("out_of_scope")
    assert p.specialist is None
    assert p.allowed_tools == ()
    assert p.retrieval is m.RetrievalRequirement.NONE
    assert p.allows_answer_model is False


def test_unknown_intent_normalizes_to_policy():
    p = rp.build_plan("something-weird")
    assert p.intent == "policy"
    assert p.specialist == "policy-agent"
    # the normalization is recorded as an explicit assumption, not silently dropped
    assert any("something-weird" in a for a in p.assumptions)


def test_every_plan_carries_version_and_timeout():
    for intent in ("policy", "hr", "calendar", "meta", "chitchat", "out_of_scope"):
        p = rp.build_plan(intent)
        assert p.plan_version == m.PLAN_VERSION
        assert p.timeout_ms > 0


# --- validate_plan --------------------------------------------------------

@pytest.mark.parametrize("intent", ["policy", "hr", "calendar", "meta", "chitchat", "out_of_scope"])
def test_valid_plans_pass_validation(intent):
    out = rp.validate_plan(rp.build_plan(intent), _principal(), _pool())
    assert out.valid is True
    assert out.action is m.ValidationAction.CONTINUE


def test_unknown_specialist_is_rejected():
    bad = rp.build_plan("hr").__class__(
        intent="hr", specialist="ghost-agent", allowed_tools=("leave_balance",),
        retrieval=m.RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=1, max_retrieval_calls=1, allows_answer_model=True, timeout_ms=1000,
    )
    out = rp.validate_plan(bad, _principal(), _pool())
    assert out.valid is False
    assert out.action is m.ValidationAction.STOP
    assert out.code == "unknown_specialist"


def test_tool_not_in_specialist_registry_is_rejected():
    # hr-agent's registry has leave_balance, not whos_out — a plan pairing them is invalid.
    bad = rp.build_plan("hr").__class__(
        intent="hr", specialist="hr-agent", allowed_tools=("whos_out",),
        retrieval=m.RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=1, max_retrieval_calls=1, allows_answer_model=True, timeout_ms=1000,
    )
    out = rp.validate_plan(bad, _principal(), _pool())
    assert out.valid is False
    assert out.action is m.ValidationAction.STOP
    assert out.code == "tool_not_in_registry"


def test_budget_over_global_cap_is_rejected():
    bad = rp.build_plan("hr").__class__(
        intent="hr", specialist="hr-agent", allowed_tools=("leave_balance",),
        retrieval=m.RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=5, max_retrieval_calls=1, allows_answer_model=True, timeout_ms=1000,
    )
    out = rp.validate_plan(bad, _principal(), _pool())
    assert out.valid is False
    assert out.action is m.ValidationAction.STOP
    assert out.code == "budget_exceeds_cap"


def test_no_specialist_plan_carrying_tools_is_rejected():
    bad = rp.build_plan("meta").__class__(
        intent="meta", specialist=None, allowed_tools=("leave_balance",),
        retrieval=m.RetrievalRequirement.NONE, needs_live_data=False,
        max_tool_calls=0, max_retrieval_calls=0, allows_answer_model=True, timeout_ms=1000,
    )
    out = rp.validate_plan(bad, _principal(), _pool())
    assert out.valid is False
    assert out.action is m.ValidationAction.STOP
    assert out.code == "invalid_plan"


def test_tool_budget_mismatch_is_rejected():
    # allow-list non-empty but zero tool budget — internally inconsistent.
    bad = rp.build_plan("hr").__class__(
        intent="hr", specialist="hr-agent", allowed_tools=("leave_balance",),
        retrieval=m.RetrievalRequirement.REQUIRED, needs_live_data=True,
        max_tool_calls=0, max_retrieval_calls=1, allows_answer_model=True, timeout_ms=1000,
    )
    out = rp.validate_plan(bad, _principal(), _pool())
    assert out.valid is False
    assert out.action is m.ValidationAction.STOP
