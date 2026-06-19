# pyrefly: ignore [missing-import]
import asyncio

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from backend.core.chat_memory import (
    ChatMemoryError,
    append_exchange,
    clear_history as clear_session_history,
    get_history as get_session_history,
    get_history_with_ids,
    get_session_summary,
    update_session_summary,
    delete_messages_before_id,
)
from backend.core.config import LLM_TIMEOUT_SECONDS, MAX_HISTORY_TOKENS
from backend.core.llm import (
    classify_query,
    get_llm_response,
    get_meta_response,
    count_tokens,
    summarize_history,
)
from backend.core.rag import list_policy_documents, retrieve_context

router = APIRouter()


# --- Schemas --- (for API testing purposes)

class ChatRequest(BaseModel):
    message: str
    session_id: str = "demo"

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    context_used: str
    sources: list[dict] = Field(default_factory=list)


# A genuine "I can't answer this" reply is one or two short sentences. A real
# answer that merely appends a "Not found in the provided documents:" note about
# a missing detail is much longer — and that note must NOT cause us to discard
# the sources the answer was actually built from.
_FULL_REFUSAL_MAX_CHARS = 320


def _is_insufficient_policy_answer(reply: str) -> bool:
    """True only when the reply is essentially a whole-answer refusal, so we can
    suppress sources/context that were retrieved but not actually used.

    Deliberately does NOT treat a trailing "Not found in the provided documents:"
    note as insufficient: that note means the model *did* answer from the
    documents and the sources remain valid.
    """
    normalized = reply.strip().lower()
    refusal_markers = [
        "do not contain enough information",
        "don't contain enough information",
        "does not contain enough information",
        "uploaded documents don't contain",
        "uploaded policy documents do not contain",
        "provided documents do not contain",
        "couldn't find specific information",
        "could not find specific information",
    ]
    if not any(marker in normalized for marker in refusal_markers):
        return False
    # Only a short, refusal-dominated reply counts; a long answer that happens to
    # mention missing details keeps its sources.
    return len(normalized) <= _FULL_REFUSAL_MAX_CHARS


# --- Endpoints ---

@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Send a message and receive an AI response with RAG context."""

    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    try:
        # Retrieve full conversation history (with DB IDs) and any existing session summary
        history = get_history_with_ids(req.session_id)
        summary = get_session_summary(req.session_id)

        # Check if conversation history is too large and summarize it if necessary
        # We need at least 5 messages to summarize (keeping the last 4 as active conversation)
        if len(history) > 4:
            temp_messages = []
            if summary:
                temp_messages.append({"role": "system", "content": f"Summary of previous conversation: {summary}"})
            for msg in history:
                temp_messages.append({"role": msg["role"], "content": msg["content"]})

            if count_tokens(temp_messages) > MAX_HISTORY_TOKENS:
                messages_to_summarize = history[:-4]
                summary_input = [{"role": m["role"], "content": m["content"]} for m in messages_to_summarize]
                
                # Perform LLM summarization
                new_summary = summarize_history(summary_input, summary)
                
                # Update DB and prune messages
                update_session_summary(req.session_id, new_summary)
                
                # Delete messages up to the last summarized message's ID
                last_summarized_id = messages_to_summarize[-1]["id"]
                delete_messages_before_id(req.session_id, last_summarized_id)
                
                # Update local values for the current LLM prompt context
                summary = new_summary
                history = history[-4:]

    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)

    # Format history to pass to LLM (without database IDs)
    formatted_history = []
    if summary:
        formatted_history.append({
            "role": "system",
            "content": f"Summary of previous conversation:\n{summary}"
        })
    for msg in history:
        formatted_history.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # Step 1: Classify before retrieval so meta and out-of-scope queries skip RAG.
    try:
        classification = await asyncio.wait_for(
            asyncio.to_thread(classify_query, req.message, formatted_history),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="The language model timed out while classifying the request. Please try again.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Step 2: Branch on classification.
    if classification == "out_of_scope":
        final_reply = "I'm a company policy assistant, so I can only help with questions about company policies and operations. Please ask a policy-related question."
        final_context = ""
        final_sources = []
    elif classification == "meta":
        try:
            final_reply = await asyncio.wait_for(
                asyncio.to_thread(
                    get_meta_response,
                    user_message=req.message,
                    history=formatted_history,
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
        final_context = ""
        final_sources = []
    else:
        # Policy query
        retrieved = retrieve_context(req.message)
        rag_context = retrieved.text if retrieved.sources else ""
        if not retrieved.sources:
            final_reply = "I couldn't find specific information on that in the handbook. Please check with HR or the Executive Director."
            final_context = ""
            final_sources = []
        else:
            try:
                reply = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_llm_response,
                        user_message=req.message,
                        context=rag_context,
                        history=formatted_history,
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

            if _is_insufficient_policy_answer(reply):
                final_reply = reply
                final_context = ""
                final_sources = []
            else:
                final_reply = reply
                final_context = retrieved.text
                final_sources = retrieved.sources

    # Step 4: Update conversation history
    try:
        append_exchange(req.session_id, req.message, final_reply)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)

    return ChatResponse(
        session_id=req.session_id,
        reply=final_reply,
        context_used=final_context,
        sources=final_sources,
    )


@router.get("/documents")
async def documents():
    """List the local PDF policy documents that make up the indexed corpus."""
    return {"documents": list_policy_documents()}


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
