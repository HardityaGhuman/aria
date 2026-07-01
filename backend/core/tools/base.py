"""core/tools/base.py
--------------------
The Tool contract, its result envelope, RBAC role ranking, and a small arg-schema
validator. The validator is the teeth behind validate-or-repair: a tool call whose
args fail its declared JSON schema is never executed."""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from backend.core.tools.principal import Principal

# Role hierarchy for tool RBAC (min_role gating). Mirrors core/auth roles.
ROLE_RANK = {"employee": 0, "manager": 1, "hr": 2}


def role_allows(have: str, need: str) -> bool:
    """True when a caller with role ``have`` may use a tool needing ``need``."""
    return ROLE_RANK.get(have, 0) >= ROLE_RANK.get(need, 0)


@dataclass
class ToolResult:
    """What a tool hands back. ``status`` is ok | error | confirmation_required.
    ``summary`` is human-readable (used for the confirmation prompt / display);
    ``data`` is the structured payload the answer model fuses into its reply."""
    status: str
    data: dict | None = None
    summary: str | None = None
    error: str | None = None


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict           # JSON schema — NO identity fields (no user_id/email)
    requires_confirmation: bool
    min_role: str

    def invoke(self, args: dict, principal: Principal) -> ToolResult: ...


# --- arg-schema validation (JSON-schema subset) ---
_PY_TYPE = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
}


def _type_ok(value, json_type: str) -> bool:
    expected = _PY_TYPE.get(json_type)
    if expected is None:
        return True  # unknown type declared — don't block
    # bool is a subclass of int in Python; reject it for integer/number.
    if json_type in ("integer", "number") and isinstance(value, bool):
        return False
    if json_type == "boolean":
        return isinstance(value, bool)
    return isinstance(value, expected)


def validate_args(schema: dict, args: dict) -> list[str]:
    """Validate ``args`` against a subset JSON schema. Returns a list of error
    strings (empty ⇒ valid). Supports type, required, and enum."""
    errors: list[str] = []
    if not isinstance(args, dict):
        return [f"arguments must be an object, got {type(args).__name__}"]

    for field in schema.get("required", []):
        if field not in args:
            errors.append(f"missing required field '{field}'")

    props = schema.get("properties", {})
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            continue  # unknown field — tolerated, not fed to the tool as identity
        json_type = spec.get("type")
        if json_type and not _type_ok(value, json_type):
            errors.append(f"field '{name}' must be of type {json_type}")
            continue
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"field '{name}' must be one of {spec['enum']}")
    return errors
