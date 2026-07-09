"""core/slack_verify.py
---------------------
Inbound-request authenticity for the Slack edge. Two independent checks:
(1) verify_slack_signature — Slack's v0 HMAC scheme over "v0:{ts}:{body}", with a
300-second replay window and a constant-time compare, so a forged or replayed Slack
POST is rejected before any work; (2) require_n8n_secret — a constant-time bearer
check so only our n8n instance can call the write endpoints. Both fail closed."""
import hashlib
import hmac
import time

from backend.core.config import N8N_SHARED_SECRET, SLACK_SIGNING_SECRET

_MAX_SKEW_SECONDS = 300


def verify_slack_signature(timestamp: str, raw_body: bytes, signature: str, *, secret=None, now=None) -> bool:
    secret = secret if secret is not None else SLACK_SIGNING_SECRET
    if not secret or not signature or not timestamp:
        return False
    now = now if now is not None else time.time()
    try:
        if abs(now - int(timestamp)) > _MAX_SKEW_SECONDS:
            return False
    except (ValueError, TypeError):
        return False
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def require_n8n_secret(authorization_header: str | None, *, expected=None) -> bool:
    expected = expected if expected is not None else N8N_SHARED_SECRET
    if not expected or not authorization_header:
        return False
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return False
    return hmac.compare_digest(authorization_header[len(prefix):], expected)
