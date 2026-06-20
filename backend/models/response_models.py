"""
models/response_models.py
-------------------------
Shapes of outgoing API responses. Explicit schemas keep the contract stable for
the frontend and self-document the Swagger UI.
"""
from pydantic import BaseModel, Field


class ChatResponse(BaseModel):
    """Response for ``POST /chat``."""

    session_id: str = Field(..., description="The conversation id this reply belongs to.")
    reply: str = Field(..., description="The assistant's answer.")
    context_used: str = Field(
        ..., description="Retrieved context behind the answer (empty when not grounded)."
    )
    sources: list[dict] = Field(
        default_factory=list,
        description="Per-chunk source metadata for the cited documents.",
    )
