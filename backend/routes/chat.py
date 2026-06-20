"""
api/routes/chat.py
------------------
Thin HTTP layer for the chat feature. Orchestration lives in
``backend.services.chat_service``; these handlers just validate, delegate, and
shape responses.
"""
import os

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import FileResponse

from backend.core.chat_memory import (
    ChatMemoryError,
    clear_history as clear_session_history,
    get_history as get_session_history,
)
from backend.core.config import DOCS_PATH
from backend.models import ChatRequest, ChatResponse, EvaluateRequest
from backend.rag import list_policy_documents
from backend.services.chat_service import generate_chat_reply, score_answer

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message and receive an AI response with RAG context."""
    result = await generate_chat_reply(req.session_id, req.message)
    return ChatResponse(
        session_id=req.session_id,
        reply=result.reply,
        context_used=result.context_used,
        sources=result.sources,
    )


@router.post("/evaluate")
async def evaluate(req: EvaluateRequest):
    """Score one answer (faithfulness / relevance) for the live metrics tab."""
    return await score_answer(req.message, req.reply, req.context_used)


@router.get("/documents")
async def documents():
    """List the local PDF policy documents that make up the indexed corpus."""
    return {"documents": list_policy_documents()}


@router.get("/documents/{filename}/download")
async def download_document(filename: str):
    """Serve a source policy PDF so users can read and cross-check answers.

    When documents move to cloud storage (e.g. Google Drive), point the frontend
    at the share link instead; this endpoint stays as the local fallback.
    """
    safe_name = os.path.basename(filename)
    file_path = os.path.join(DOCS_PATH, safe_name)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Document not found.")
    return FileResponse(file_path, media_type="application/pdf", filename=safe_name)


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
