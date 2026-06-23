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
from backend.models import ChatRequest, ChatResponse
from backend.rag import list_policy_documents
from backend.services.chat_service import generate_chat_reply

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


_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


@router.get("/documents")
async def documents():
    """List the local policy documents that make up the indexed corpus.

    Filenames are paths relative to the docs root (e.g. ``hr/employment-basics.md``)
    because the corpus is organized into department subfolders.
    """
    return {"documents": list_policy_documents()}


@router.get("/documents/{filename:path}/download")
async def download_document(filename: str):
    """Serve a source policy document so users can read and cross-check answers.

    ``filename`` is the path relative to the docs root and may include a
    department subfolder. We resolve it under ``DOCS_PATH`` and reject anything
    that escapes that directory (path-traversal guard).
    """
    docs_root = os.path.abspath(DOCS_PATH)
    requested = os.path.normpath(os.path.join(docs_root, filename))
    if requested != docs_root and not requested.startswith(docs_root + os.sep):
        raise HTTPException(status_code=400, detail="Invalid document path.")
    if not os.path.isfile(requested):
        raise HTTPException(status_code=404, detail="Document not found.")

    extension = os.path.splitext(requested)[1].lower()
    media_type = _MEDIA_TYPES.get(extension, "application/octet-stream")
    return FileResponse(
        requested, media_type=media_type, filename=os.path.basename(requested)
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
