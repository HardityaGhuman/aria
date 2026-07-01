"""core/tools/principal.py
-------------------------
The Principal value object — the single source of identity a tool may use.

Built server-side from the verified JWT (the get_current_user dict). Tools never
read a user id/email out of LLM-generated arguments; that is the identity-confusion
defense (bob's question can never act as alice). Frozen so it cannot be mutated
mid-loop."""
from dataclasses import dataclass

_DEFAULT_REGION = "us"


@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str | None
    role: str
    region: str


def principal_from_user(user: dict) -> Principal:
    """Build a Principal from the get_current_user claims dict."""
    return Principal(
        user_id=int(user["id"]),
        email=user.get("email"),
        role=user["role"],
        region=user.get("region", _DEFAULT_REGION),
    )
