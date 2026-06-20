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


class EvaluateRequest(BaseModel):
    """Body for ``POST /chat/evaluate`` — scores one already-generated answer."""

    message: str = Field(..., description="The original user question.")
    reply: str = Field(..., description="The assistant's answer to score.")
    context_used: str = Field(
        default="",
        description="The retrieved context the answer was grounded in.",
    )
