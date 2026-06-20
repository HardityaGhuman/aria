"""
services/chat_service.py
------------------------
Application logic for the chat flow: conversation memory + summarisation, query
classification, retrieval-augmented answering, the grounding guardrails, and
answer scoring. Routes delegate here and stay thin.
"""
import asyncio
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
from fastapi import HTTPException

from backend.core.chat_memory import (
    ChatMemoryError,
    append_exchange,
    delete_messages_before_id,
    get_history_with_ids,
    get_session_summary,
    update_session_summary,
)
from backend.core.config import (
    LLM_TIMEOUT_SECONDS,
    MAX_HISTORY_TOKENS,
    QUERY_REWRITE_ENABLED,
)
from backend.core.evaluation import evaluate_answer
from backend.core.llm import (
    classify_query,
    count_tokens,
    get_llm_response,
    get_meta_response,
    summarize_history,
)
from backend.core.logging import get_logger
from backend.rag import retrieve_context, rewrite_query

logger = get_logger(__name__)

# Shown when an off-topic query slips through; also detected (see _is_refusal) so
# we never attach misleading sources to it.
REFUSAL_MESSAGE = (
    "I'm a company policy assistant, so I can only help with questions about "
    "company policies and operations. Please ask a policy-related question."
)
NO_RESULTS_MESSAGE = (
    "I couldn't find specific information on that in the handbook. "
    "Please check with HR or the Executive Director."
)

# A genuine "I can't answer this" reply is one or two short sentences. A real
# answer that merely appends a "Not found in the provided documents:" note about
# a missing detail is much longer — that note must NOT discard valid sources.
_FULL_REFUSAL_MAX_CHARS = 320


@dataclass
class ChatResult:
    reply: str
    context_used: str
    sources: list[dict]


def _is_refusal(reply: str) -> bool:
    """True when the model fell back to the off-topic refusal; such replies are
    not grounded, so their retrieved context must be discarded."""
    return reply.strip().lower().startswith("i'm a company policy assistant")


def _is_insufficient_policy_answer(reply: str) -> bool:
    """True only when the reply is essentially a whole-answer refusal, so we can
    suppress sources/context that were retrieved but not actually used. Does NOT
    treat a trailing "Not found..." note on a real answer as insufficient."""
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
    return len(normalized) <= _FULL_REFUSAL_MAX_CHARS


async def _run_blocking(fn, *args, timeout_detail: str, **kwargs):
    """Offload a blocking LLM call to a worker thread with a timeout, mapping
    failures to HTTP errors so the routes don't have to."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=timeout_detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _prepare_history(session_id: str) -> list[dict]:
    """Load conversation history + summary, summarising and pruning older
    messages once the token budget is exceeded. Returns the formatted history
    (with an optional leading summary message) ready for the LLM."""
    try:
        history = get_history_with_ids(session_id)
        summary = get_session_summary(session_id)

        # Need at least 5 messages to summarise (keep the last 4 active).
        if len(history) > 4:
            temp_messages = []
            if summary:
                temp_messages.append({"role": "system", "content": f"Summary of previous conversation: {summary}"})
            for msg in history:
                temp_messages.append({"role": msg["role"], "content": msg["content"]})

            if count_tokens(temp_messages) > MAX_HISTORY_TOKENS:
                messages_to_summarize = history[:-4]
                summary_input = [{"role": m["role"], "content": m["content"]} for m in messages_to_summarize]

                new_summary = summarize_history(summary_input, summary)
                update_session_summary(session_id, new_summary)
                delete_messages_before_id(session_id, messages_to_summarize[-1]["id"])

                summary = new_summary
                history = history[-4:]
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)

    formatted_history = []
    if summary:
        formatted_history.append({"role": "system", "content": f"Summary of previous conversation:\n{summary}"})
    for msg in history:
        formatted_history.append({"role": msg["role"], "content": msg["content"]})
    return formatted_history


async def _resolve_search_query(message: str, formatted_history: list[dict]) -> str:
    """Rewrite the message into a standalone search query (history-aware) so
    follow-ups retrieve correctly. Falls back to the original on any failure."""
    if not QUERY_REWRITE_ENABLED:
        return message
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(rewrite_query, message, formatted_history),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except Exception:
        return message


async def _answer_policy_query(message: str, formatted_history: list[dict]) -> ChatResult:
    search_query = await _resolve_search_query(message, formatted_history)
    retrieved = retrieve_context(search_query)
    if not retrieved.sources:
        return ChatResult(NO_RESULTS_MESSAGE, "", [])

    reply = await _run_blocking(
        get_llm_response,
        timeout_detail="The language model timed out while generating a response. Please try again.",
        user_message=message,
        context=retrieved.text,
        history=formatted_history,
    )

    # A refusal or "not enough info" reply isn't grounded in the retrieved
    # chunks, so drop the context and sources.
    if _is_insufficient_policy_answer(reply) or _is_refusal(reply):
        return ChatResult(reply, "", [])
    return ChatResult(reply, retrieved.text, retrieved.sources)


async def generate_chat_reply(session_id: str, message: str) -> ChatResult:
    """Full chat flow: prepare history, classify, answer, persist the exchange."""
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    formatted_history = _prepare_history(session_id)

    # Classify before retrieval so meta and out-of-scope queries skip RAG.
    classification = await _run_blocking(
        classify_query,
        message,
        formatted_history,
        timeout_detail="The language model timed out while classifying the request. Please try again.",
    )

    if classification == "out_of_scope":
        result = ChatResult(REFUSAL_MESSAGE, "", [])
    elif classification == "meta":
        reply = await _run_blocking(
            get_meta_response,
            timeout_detail="The language model timed out while generating a response. Please try again.",
            user_message=message,
            history=formatted_history,
        )
        result = ChatResult(reply, "", [])
    else:
        result = await _answer_policy_query(message, formatted_history)

    try:
        append_exchange(session_id, message, result.reply)
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)

    return result


async def score_answer(message: str, reply: str, context: str) -> dict:
    """Score one already-generated answer for the live evaluation tab."""
    return await _run_blocking(
        evaluate_answer,
        timeout_detail="Evaluation timed out.",
        question=message,
        answer=reply,
        context=context,
    )
