"""Specialist value objects + scoped registries: each specialist sees ONLY its own
tools (no superset), and the structured result contract carries typed fields."""
import dataclasses

from backend.core.agents.build import build_specialists
from backend.core.agents.specialist import SpecialistResult
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=1, email="e@x.test", role="employee", region="us")


def _by_name(specialists):
    return {s.name: s for s in specialists}


def test_build_specialists_returns_hr_and_policy():
    specs = _by_name(build_specialists())
    assert set(specs) == {"hr-agent", "policy-agent"}
    assert specs["hr-agent"].uses_tools is True
    assert specs["policy-agent"].uses_tools is False
    assert specs["hr-agent"].min_role == "employee"
    assert specs["policy-agent"].min_role == "employee"


def test_hr_agent_registry_is_scoped_to_leave_balance():
    hr = _by_name(build_specialists())["hr-agent"]
    names = [s["function"]["name"] for s in hr.registry.specs_for(EMPLOYEE)]
    assert names == ["leave_balance"]


def test_policy_agent_registry_has_no_tools():
    policy = _by_name(build_specialists())["policy-agent"]
    assert policy.registry.specs_for(EMPLOYEE) == []


def test_specialist_result_defaults():
    r = SpecialistResult(specialist="policy-agent")
    assert r.tool_results == []
    assert r.tool_note is None
    assert r.status == "no_tools"


def test_specialist_is_frozen():
    hr = _by_name(build_specialists())["hr-agent"]
    try:
        hr.name = "x"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    assert False, "Specialist must be frozen"
