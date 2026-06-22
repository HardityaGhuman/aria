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
