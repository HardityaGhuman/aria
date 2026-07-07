"""§4.1 client-contract follow-through — the `ChatResponse.status` field is a typed
allow-list, not a free string. `partial` and `tool_unavailable` (added for the
control layer) must survive the envelope unchanged, and a value outside the set
must be rejected at the boundary so an internal terminal state can never leak
verbatim to the client."""
import pytest
from pydantic import ValidationError

from backend.models.response_models import ChatResponse


def _envelope(status: str) -> ChatResponse:
    return ChatResponse(answer="a", sources=[], latency_ms=1,
                        session_id="s1", status=status)


@pytest.mark.parametrize(
    "status",
    ["ok", "partial", "no_results", "blocked", "refused", "tool_unavailable"],
)
def test_client_facing_statuses_survive_the_envelope(status):
    env = _envelope(status)
    assert env.status == status
    assert env.model_dump()["status"] == status


@pytest.mark.parametrize("status", ["error", "invalid_plan", "internal_error", ""])
def test_internal_or_bogus_statuses_are_rejected(status):
    # invalid_plan / internal_error / a bare "error" are never client-facing —
    # the Literal keeps them out of the wire contract.
    with pytest.raises(ValidationError):
        _envelope(status)
