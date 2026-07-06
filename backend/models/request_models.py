"""
models/request_models.py
------------------------
Shapes of incoming API request bodies. Pydantic gives us validation, type
coercion, and auto-generated OpenAPI docs for free.
"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Body for ``POST /chat``."""

    # Capped at the edge: an unbounded message inflates classifier/history/telemetry
    # cost and, once the agent path is live, multiplies per-turn LLM calls. 8000
    # chars (~2k tokens) sits well under the context budget. Whitespace-only still
    # passes here (length ≥ 1) and is caught downstream with a clean error event.
    message: str = Field(..., min_length=1, max_length=8000, description="The user's question.")
    # session_id becomes a PK and a telemetry/log key — constrain its shape so it
    # can't smuggle path-like or oversized values. Pattern covers UUIDs + the default.
    session_id: str = Field(
        default="demo",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Conversation id; each session keeps its own history.",
    )


class RenameSessionRequest(BaseModel):
    """Body for ``PATCH /chat/sessions/{session_id}``."""

    title: str = Field(..., min_length=1, max_length=200, description="New session title.")


class UpdatePreferencesRequest(BaseModel):
    """Body for ``PUT /me/preferences``. Any field may be omitted/null to clear it."""

    tone: str | None = Field(None, max_length=50, description="e.g. friendly, formal, concise.")
    response_length: str | None = Field(None, max_length=50, description="short | medium | long.")
    language: str | None = Field(None, max_length=50, description="Preferred answer language.")
