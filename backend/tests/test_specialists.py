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


def test_hr_agent_registry_is_scoped_to_its_read_tools():
    hr = _by_name(build_specialists())["hr-agent"]
    names = [s["function"]["name"] for s in hr.registry.specs_for(EMPLOYEE)]
    # HR-agent sees ONLY its own read tools — leave_balance + whos_out, no superset.
    assert names == ["leave_balance", "whos_out"]


def test_hr_agent_leave_balance_invokes_against_mock_hris():
    # The real composition root wires leave_balance → MockHRIS; the seeded
    # employee@gsvh.test row is 20 total − 8 used = 12 remaining.
    hr = _by_name(build_specialists())["hr-agent"]
    result = hr.registry.invoke(
        "leave_balance", {},
        Principal(user_id=3, email="employee@gsvh.test", role="employee", region="us"),
    )
    assert result.status == "ok"
    assert result.data["remaining"] == 20 - 8


def test_hr_agent_whos_out_invokes_through_registry():
    # The no-arg read passes the registry's RBAC re-check + validate path and
    # returns the seeded team OOO view.
    hr = _by_name(build_specialists())["hr-agent"]
    result = hr.registry.invoke("whos_out", {}, EMPLOYEE)
    assert result.status == "ok"
    assert len(result.data["out"]) >= 1


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
