from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.gemini import get_gemini_response
from backend.core.rag import retrieve_context

router = APIRouter()

# In-memory session store (swap for Redis in production)
_sessions: dict[str, list[dict]] = {}


# --- Schemas ---

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    context_used: str


# --- Endpoints ---

@router.post("/", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message and receive an AI response with RAG context."""

    history = _sessions.get(req.session_id, [])

    # Step 1: Retrieve relevant context from the vector store
    context = retrieve_context(req.message)

    # Step 2: Get Gemini response
    try:
        reply = get_gemini_response(
            user_message=req.message,
            context=context,
            history=history
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Step 3: Update conversation history
    history.append({"role": "user", "parts": [req.message]})
    history.append({"role": "model", "parts": [reply]})
    _sessions[req.session_id] = history

    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        context_used=context
    )


@router.get("/history/{session_id}")
async def get_history(session_id: str):
    """Retrieve conversation history for a session."""
    history = _sessions.get(session_id, [])
    return {"session_id": session_id, "history": history}


@router.delete("/history/{session_id}")
async def clear_history(session_id: str):
    """Clear conversation history for a session."""
    _sessions.pop(session_id, None)
    return {"message": f"Session {session_id} cleared."}