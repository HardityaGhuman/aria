"""
models/request_models.py
------------------------
Shapes of incoming API request bodies. Pydantic gives us validation, type
coercion, and auto-generated OpenAPI docs for free.
"""
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Body for ``POST /chat``."""

    message: str = Field(..., description="The user's question.")
    session_id: str = Field(
        default="demo",
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
