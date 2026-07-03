"""Principal is the ONLY identity a tool may use. It is built server-side from the
verified JWT claims (the get_current_user dict) — never from LLM-supplied args."""
import dataclasses

import pytest

from backend.core.tools.principal import Principal, principal_from_user


def test_built_from_user_dict():
    p = principal_from_user({"id": 7, "role": "manager", "region": "india", "email": "m@x.test"})
    assert (p.user_id, p.role, p.region, p.email) == (7, "manager", "india", "m@x.test")


def test_region_defaults_to_us_when_absent():
    p = principal_from_user({"id": 1, "role": "employee"})
    assert p.region == "us"
    assert p.email is None


def test_principal_is_frozen():
    # Immutable so identity cannot be mutated mid-loop by any tool.
    p = principal_from_user({"id": 1, "role": "employee", "region": "us"})
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.user_id = 999
