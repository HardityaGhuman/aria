"""routes/deps.py
---------------
Shared FastAPI dependencies for the WRITE routes.

Why this exists: `start_trace()` was called only in `services/chat_service.py`, i.e. on
the read path. Every write request therefore ran outside a trace context, so the graph's
`case_*` events AND the `extract` LLM span (emitted deep inside `core/llm._invoke`) both
carried `trace_id: null` — the single model call inside a Case could not be joined to
that Case, or to anything else. The audit trail said WHAT happened to the business
object; nothing said HOW the run behaved.

These dependencies open one trace for the whole request and reset it in a `finally`, so
everything emitted underneath — nodes, write attempts, breaker trips, LLM spans — shares
one `trace_id`, and `case_id` stitches them to the Case. A dependency (not a decorator)
because it composes with the auth dependency the route already declares, and because
`finally` in a `yield` dependency runs even when the endpoint raises.

The ContextVar is set in the same task that calls the endpoint, and anyio copies the
context into the worker thread for sync endpoints, so both flavours see it.
"""
# pyrefly: ignore [missing-import]
from fastapi import Depends

from backend.core.auth import get_current_user, require_role
from backend.core.trace import reset_trace, start_trace


async def open_trace():
    """Trace an unauthenticated-by-JWT route (the Slack/n8n leave edge). The caller is
    identified downstream by Slack id, so there is no user_id to attach — the trace_id
    is the point."""
    token = start_trace()
    try:
        yield
    finally:
        reset_trace(token)


async def traced_user(user: dict = Depends(get_current_user)):
    """JWT-native write route: authenticate, then trace the request under that user."""
    token = start_trace(user_id=user["id"])
    try:
        yield user
    finally:
        reset_trace(token)


def traced_role(role: str):
    """Same, for an admin surface (DLQ / replay / breaker reset). The RBAC check runs
    first: an unauthorized caller is 403'd before a trace is ever opened."""
    checker = require_role(role)

    async def _dep(user: dict = Depends(checker)):
        token = start_trace(user_id=user["id"])
        try:
            yield user
        finally:
            reset_trace(token)

    return _dep
