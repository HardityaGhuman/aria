"""The arg-schema validator underwrites validate-or-repair: an arg set that fails
the tool's declared JSON schema is never executed. Hand-rolled subset validator —
no jsonschema dependency."""
from backend.core.tools.base import ROLE_RANK, role_allows, validate_args

SCHEMA = {
    "type": "object",
    "properties": {
        "days": {"type": "integer"},
        "kind": {"type": "string", "enum": ["pto", "sick"]},
    },
    "required": ["days"],
}


def test_valid_args_return_no_errors():
    assert validate_args(SCHEMA, {"days": 3, "kind": "pto"}) == []


def test_missing_required_field_is_an_error():
    errors = validate_args(SCHEMA, {"kind": "pto"})
    assert any("days" in e for e in errors)


def test_wrong_type_is_an_error():
    errors = validate_args(SCHEMA, {"days": "three"})
    assert any("days" in e and "integer" in e for e in errors)


def test_value_outside_enum_is_an_error():
    errors = validate_args(SCHEMA, {"days": 1, "kind": "vacation"})
    assert any("kind" in e for e in errors)


def test_bool_is_not_accepted_as_integer():
    # In Python bool is a subclass of int; the validator must reject True for an integer.
    errors = validate_args(SCHEMA, {"days": True})
    assert any("days" in e for e in errors)


def test_role_rank_ordering():
    assert ROLE_RANK["employee"] < ROLE_RANK["manager"] < ROLE_RANK["hr"]
    assert role_allows("hr", "manager") is True
    assert role_allows("employee", "manager") is False
    assert role_allows("manager", "manager") is True
