"""ChatRequest caps: an unbounded message inflates classifier/history/telemetry
cost, and an attacker-chosen huge/oddly-shaped session_id becomes a PK and a log
key. The model rejects both at the edge before any of that runs."""
import pytest
from pydantic import ValidationError

from backend.models.request_models import ChatRequest


def test_normal_request_is_accepted():
    req = ChatRequest(message="How many PTO days do I get?", session_id="abc-123_XY")
    assert req.message.startswith("How many")
    assert req.session_id == "abc-123_XY"


def test_default_session_id_is_valid():
    # The default must itself satisfy the pattern (clients may omit session_id).
    assert ChatRequest(message="hi").session_id == "demo"


def test_oversized_message_is_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 8001)


def test_empty_message_is_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(message="")


def test_malformed_session_id_is_rejected():
    # A slash would let a session_id smuggle path-like shapes into PKs/log keys.
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", session_id="../../etc/passwd")


def test_oversized_session_id_is_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", session_id="a" * 65)
