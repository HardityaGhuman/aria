"""core/tools/principal.py
-------------------------
The Principal value object — the single source of identity a tool may use.

Built server-side from the verified JWT (the get_current_user dict). Tools never
read a user id/email out of LLM-generated arguments; that is the identity-confusion
defense (bob's question can never act as alice). Frozen so it cannot be mutated
mid-loop.

``load_principal`` rebuilds it from the users table instead of the JWT. Write Cases sleep
at ``pending_approval`` for hours/days, so an identity captured at request time is stale
by the time the write runs: the requester may have been demoted, region-changed, or
offboarded. The graphs therefore checkpoint only a ``user_id`` and reload identity at
every node that needs it; ``None`` (user gone) fails the Case closed."""
from dataclasses import dataclass

from backend.core.users import get_user_by_id

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


def load_principal(user_id: int) -> Principal | None:
    """Rebuild the Principal from the users table (live identity truth).

    Returns None when the user no longer exists — callers must fail closed, never
    fall back to a checkpointed identity."""
    user = get_user_by_id(user_id)
    return principal_from_user(user) if user else None
