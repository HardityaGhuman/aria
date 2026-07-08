import hashlib
import hmac
import time

from backend.core.slack_verify import verify_slack_signature, require_n8n_secret

SECRET = "shhh"


def _sign(ts, body):
    base = f"v0:{ts}:{body}".encode()
    return "v0=" + hmac.new(SECRET.encode(), base, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    ts = str(int(time.time()))
    body = "token=x&user_id=U1"
    sig = _sign(ts, body)
    assert verify_slack_signature(ts, body.encode(), sig, secret=SECRET) is True


def test_tampered_body_fails():
    ts = str(int(time.time()))
    sig = _sign(ts, "original")
    assert verify_slack_signature(ts, b"tampered", sig, secret=SECRET) is False


def test_stale_timestamp_rejected():
    ts = str(int(time.time()) - 600)  # 10 min old
    body = "x"
    sig = _sign(ts, body)
    assert verify_slack_signature(ts, body.encode(), sig, secret=SECRET) is False


def test_n8n_bearer():
    assert require_n8n_secret("Bearer topsecret", expected="topsecret") is True
    assert require_n8n_secret("Bearer wrong", expected="topsecret") is False
    assert require_n8n_secret(None, expected="topsecret") is False
