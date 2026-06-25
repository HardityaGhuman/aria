"""
api/routes/chat.py
------------------
Thin HTTP layer for the chat feature. Orchestration lives in
``backend.services.chat_service``; these handlers just validate, delegate, and
shape responses.
"""
import os
import time

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException

from backend.core.auth import get_current_user, regions_for_user, tiers_for_role
from backend.core.chat_memory import (
    ChatMemoryError,
    clear_history as clear_session_history,
    get_history as get_session_history,
)
from backend.models import ChatRequest, ChatResponse
from backend.models.response_models import Source
from backend.services.chat_service import generate_chat_reply


def _to_sources(raw: list[dict]) -> list[Source]:
    """Map the retriever's per-chunk dicts onto the frozen ``Source`` shape."""
    sources = []
    for item in raw:
        document_id = item.get("source", "")
        sources.append(Source(
            document_id=document_id,
            file=os.path.basename(document_id),
            section=item.get("section"),
            source_type=item.get("access_tier"),
        ))
    return sources

# Every chat route requires an authenticated user (any role). The gate is at the
# router level so no endpoint can be added unsecured by accident.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    """Send a message and receive an AI response with RAG context.

    The user's role gates which document tiers are retrievable (RBAC).
    """
    started = time.perf_counter()
    result = await generate_chat_reply(
        req.session_id,
        req.message,
        allowed_tiers=tiers_for_role(user["role"]),
        allowed_regions=regions_for_user(user["region"]),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ChatResponse(
        answer=result.reply,
        sources=_to_sources(result.sources),
        latency_ms=latency_ms,
        session_id=req.session_id,
        status=result.status,
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """Retrieve conversation history for a session."""
    try:
        history = get_session_history(session_id)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return {"session_id": session_id, "history": history}


@router.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Clear conversation history for a session."""
    try:
        clear_session_history(session_id)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return {"message": f"Session {session_id} cleared."}
