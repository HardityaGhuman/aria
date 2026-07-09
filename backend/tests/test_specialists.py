"""Specialist value objects + scoped registries: each specialist sees ONLY its own
tools (no superset), and the structured result contract carries typed fields."""
import dataclasses

from backend.core.agents.build import build_specialists
from backend.core.agents.specialist import SpecialistResult
from backend.core.tools.principal import Principal

EMPLOYEE = Principal(user_id=1, email="e@x.test", role="employee", region="us")


def _by_name(specialists):
    return {s.name: s for s in specialists}


def test_build_specialists_returns_hr_policy_and_calendar():
    specs = _by_name(build_specialists())
    assert set(specs) == {"hr-agent", "policy-agent", "calendar-agent"}
    assert specs["hr-agent"].uses_tools is True
    assert specs["policy-agent"].uses_tools is False
    assert specs["calendar-agent"].uses_tools is True
    assert specs["hr-agent"].min_role == "employee"
    assert specs["policy-agent"].min_role == "employee"
    assert specs["calendar-agent"].min_role == "employee"


def test_hr_agent_registry_is_scoped_to_only_leave_balance():
    hr = _by_name(build_specialists())["hr-agent"]
    names = [s["function"]["name"] for s in hr.registry.specs_for(EMPLOYEE)]
    # §5.2: HR-agent sees ONLY leave_balance — whos_out moved to the calendar-agent.
    assert names == ["leave_balance"]


def test_calendar_agent_registry_is_scoped_to_only_whos_out():
    cal = _by_name(build_specialists())["calendar-agent"]
    names = [s["function"]["name"] for s in cal.registry.specs_for(EMPLOYEE)]
    # §5.3: calendar-agent sees ONLY whos_out — no leave_balance, no superset.
    assert names == ["whos_out"]


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


def test_hr_agent_no_longer_exposes_whos_out():
    # Registry isolation: whos_out must not be reachable through the hr-agent.
    hr = _by_name(build_specialists())["hr-agent"]
    assert hr.registry.get("whos_out") is None


def test_calendar_agent_whos_out_invokes_through_registry():
    # An explicit window (independent of the wall clock) passes the registry's RBAC
    # re-check + arg-schema validation and returns the seeded team OOO view.
    cal = _by_name(build_specialists())["calendar-agent"]
    result = cal.registry.invoke(
        "whos_out", {"start_date": "2026-07-01", "end_date": "2026-07-31"}, EMPLOYEE)
    assert result.status == "ok"
    assert len(result.data["out"]) >= 1
    # Display-safe only — no private calendar fields reach the tool result.
    for row in result.data["out"]:
        assert set(row) == {"name", "until"}


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


def test_no_read_specialist_exposes_submit_leave():
    from backend.core.agents.build import build_specialists
    for spec in build_specialists():
        assert spec.registry.get("submit_leave") is None
