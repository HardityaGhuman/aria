# pyrefly: ignore [missing-import]
import asyncio

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from backend.core.config import LLM_TIMEOUT_SECONDS
from backend.core.llm import get_llm_response
from backend.core.rag import initialize_vectorstore, list_policy_documents, retrieve_context

router = APIRouter()

# Lightweight in-memory chat history for local demos.
_sessions: dict[str, list[dict]] = {}
MAX_HISTORY_MESSAGES = 8


# --- Schemas --- (for API testing purposes)

class ChatRequest(BaseModel):
    message: str
    session_id: str = "demo"

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    context_used: str
    sources: list[dict] = Field(default_factory=list)


# --- Endpoints ---

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message and receive an AI response with RAG context."""

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    history = _sessions.get(req.session_id, [])[-MAX_HISTORY_MESSAGES:]
    normalized_message = req.message.strip().lower().strip(".!?")
    if normalized_message in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}:
        reply = "Hello! Ask me a specific question about the uploaded company policy documents."
        history.append({"role": "user", "content": req.message})
        history.append({"role": "assistant", "content": reply})
        _sessions[req.session_id] = history[-MAX_HISTORY_MESSAGES:]
        return ChatResponse(
            session_id=req.session_id,
            reply=reply,
            context_used="",
            sources=[],
        )

    # Step 1: Retrieve relevant context from the vector store
    retrieved = retrieve_context(req.message)
    if not retrieved.sources:
        reply = (
            "The uploaded policy documents do not contain enough information to answer that question. "
            "Please try a more specific policy question or contact the relevant internal team."
        )
        return ChatResponse(
            session_id=req.session_id,
            reply=reply,
            context_used=retrieved.text,
            sources=[],
        )

    # Step 2: Get LLM response
    try:
        reply = await asyncio.wait_for(
            asyncio.to_thread(
                get_llm_response,
                user_message=req.message,
                context=retrieved.text,
                history=history,
            ),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The language model timed out while generating a response. Please try again.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Step 3: Update conversation history
    history.append({"role": "user", "content": req.message})
    history.append({"role": "assistant", "content": reply})
    _sessions[req.session_id] = history[-MAX_HISTORY_MESSAGES:]

    return ChatResponse(
        session_id=req.session_id,
        reply=reply,
        context_used=retrieved.text,
        sources=retrieved.sources,
    )


@router.post("/reindex")
async def reindex_documents():
    """Rebuild the vector index for new or changed local policy files."""
    try:
        stats = initialize_vectorstore()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"message": "Policy documents indexed.", "stats": stats}


@router.get("/documents")
async def documents():
    """List local PDF policy documents available for ingestion."""
    return {"documents": list_policy_documents()}


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
