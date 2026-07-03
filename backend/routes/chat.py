"""
api/routes/chat.py
------------------
Thin HTTP layer for the chat feature. Orchestration lives in
``backend.services.chat_service``; these handlers just validate, delegate, and
shape responses.
"""
import json
import time

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request
# pyrefly: ignore [missing-import]
from sse_starlette.sse import EventSourceResponse

from backend.core.auth import get_current_user, regions_for_user, tiers_for_role
from backend.core.tools.principal import principal_from_user
from backend.core.config import RATE_LIMIT_CHAT
from backend.core.ratelimit import limiter
from backend.core.chat_memory import (
    ChatMemoryError,
    clear_history as clear_session_history,
    get_history as get_session_history,
)
from backend.core.session_store import session_store
from backend.models import ChatRequest, ChatResponse
from backend.models.request_models import RenameSessionRequest
from backend.models.response_models import (
    CreateSessionResponse,
    HistoryResponse,
    MessageResponse,
    SessionInfo,
    Source,
)
from backend.services.chat_service import (
    generate_chat_reply,
    stream_chat_reply,
    to_source_dicts,
)


def _require_owned_session(session_id: str, user: dict) -> None:
    """403 unless ``session_id`` belongs to the caller; 404 if it doesn't exist.

    The single ownership gate for session mutations — keeps user A out of user
    B's conversations."""
    owner = session_store.owner_of(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if owner != user["id"]:
        raise HTTPException(status_code=403, detail="Not your session.")


def _require_owned_or_new_session(session_id: str, user: dict) -> None:
    """Gate for the chat send path: allow a brand-new session (no owner yet — it
    will be stamped to the caller on first write) or one the caller already owns,
    but reject sending into someone else's session.

    Without this, user A could POST a message into user B's ``session_id``: B's
    history loads as context and a ``meta`` question ("recap our conversation")
    reads B's private messages back to A. A read-as-write IDOR."""
    owner = session_store.owner_of(session_id)
    if owner is not None and owner != user["id"]:
        raise HTTPException(status_code=403, detail="Not your session.")


# Every chat route requires an authenticated user (any role). The gate is at the
# router level so no endpoint can be added unsecured by accident.
router = APIRouter(dependencies=[Depends(get_current_user)])


@router.post("", response_model=ChatResponse)
@limiter.limit(RATE_LIMIT_CHAT)
async def chat(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    """Send a message and receive an AI response with RAG context.

    The user's role gates which document tiers are retrievable (RBAC).
    """
    _require_owned_or_new_session(req.session_id, user)
    started = time.perf_counter()
    result = await generate_chat_reply(
        req.session_id,
        req.message,
        allowed_tiers=tiers_for_role(user["role"]),
        allowed_regions=regions_for_user(user["region"]),
        owner_user_id=user["id"],
        principal=principal_from_user(user),
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ChatResponse(
        answer=result.reply,
        sources=[Source(**s) for s in to_source_dicts(result.sources)],
        latency_ms=latency_ms,
        session_id=req.session_id,
        status=result.status,
    )


@router.post("/stream")
@limiter.limit(RATE_LIMIT_CHAT)
async def chat_stream(request: Request, req: ChatRequest, user: dict = Depends(get_current_user)):
    """Stream the answer token-by-token over SSE.

    Same auth/RBAC as ``POST /chat``; the body is identical. Emits typed events
    (token/sources/done/error). The service yields event dicts with a structured
    ``data`` payload; here we JSON-encode that payload onto the SSE ``data`` field.
    """
    _require_owned_or_new_session(req.session_id, user)

    async def event_publisher():
        async for event in stream_chat_reply(
            req.session_id,
            req.message,
            allowed_tiers=tiers_for_role(user["role"]),
            allowed_regions=regions_for_user(user["region"]),
            owner_user_id=user["id"],
            principal=principal_from_user(user),
        ):
            yield {"event": event["event"], "data": json.dumps(event["data"])}

    return EventSourceResponse(event_publisher())


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(user: dict = Depends(get_current_user)):
    """List the caller's own conversations, most recently active first."""
    try:
        rows = session_store.list_for_owner(user["id"])
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return [
        SessionInfo(
            session_id=row["session_id"],
            title=row.get("title"),
            updated_at=row["updated_at"].isoformat() if row.get("updated_at") else None,
        )
        for row in rows
    ]


@router.post("/sessions", response_model=CreateSessionResponse, status_code=201)
async def create_session(user: dict = Depends(get_current_user)):
    """Create a new empty conversation owned by the caller."""
    try:
        session_id = session_store.create(user["id"])
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return CreateSessionResponse(session_id=session_id)


@router.patch("/sessions/{session_id}", response_model=SessionInfo)
async def rename_session(
    session_id: str, req: RenameSessionRequest, user: dict = Depends(get_current_user)
):
    """Rename one of the caller's conversations."""
    _require_owned_session(session_id, user)
    try:
        session_store.rename(session_id, req.title)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return SessionInfo(session_id=session_id, title=req.title, updated_at=None)


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    """Delete one of the caller's conversations and its messages."""
    _require_owned_session(session_id, user)
    try:
        session_store.delete(session_id)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return {"message": f"Session {session_id} deleted."}


@router.get("/history/{session_id}", response_model=HistoryResponse)
async def get_history(session_id: str, user: dict = Depends(get_current_user)):
    """Retrieve conversation history for one of the caller's own sessions."""
    _require_owned_session(session_id, user)
    try:
        history = get_session_history(session_id)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return {"session_id": session_id, "history": history}


@router.delete("/history/{session_id}", response_model=MessageResponse)
async def clear_history(session_id: str, user: dict = Depends(get_current_user)):
    """Clear conversation history for one of the caller's own sessions."""
    _require_owned_session(session_id, user)
    try:
        clear_session_history(session_id)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return {"message": f"Session {session_id} cleared."}
