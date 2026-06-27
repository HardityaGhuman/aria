"""
services/chat_service.py
------------------------
Application logic for the chat flow: conversation memory + summarisation, query
classification, retrieval-augmented answering, the grounding guardrails, and
answer scoring. Routes delegate here and stay thin.
"""
import asyncio
import os
import time
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
from backend.core.llm import (
    classify_query,
    count_tokens,
    get_llm_response,
    get_meta_response,
    stream_llm_response,
    summarize_history,
)
from backend.core.errors import AppError
from backend.core.logging import get_logger
from backend.core.preferences import (
    PreferencesError,
    format_preferences,
    get_preferences,
)
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
CONFIDENTIAL_MESSAGE = (
    "That information is restricted and isn't available at your access level. "
    "Please contact {contact} for details."
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
    # ok | no_results | blocked | refused — surfaced in the response envelope so
    # the client can branch on outcome without string-matching the prose.
    status: str = "ok"


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
    failures to the uniform error envelope so the client sees one error shape.

    Timeouts → ``llm_timeout``; an already-enveloped ``AppError`` (e.g. the
    retry wrapper's ``llm_error``) passes through unchanged; any other failure →
    ``llm_error`` with the real cause logged server-side (never leaked to the
    client as ``str(e)``)."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except asyncio.TimeoutError:
        raise AppError("llm_timeout", timeout_detail, status_code=504)
    except AppError:
        raise
    except Exception:
        logger.exception("LLM call failed in %s", getattr(fn, "__name__", "unknown"))
        raise AppError(
            "llm_error",
            "The language model failed to respond. Please try again.",
            status_code=502,
        )


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


def _preferences_note(owner_user_id: int | None) -> str | None:
    """Build the prompt block for a user's durable preferences, or None.

    Best-effort: a preferences DB hiccup must never break a chat answer, so we
    swallow PreferencesError and just skip personalization."""
    if owner_user_id is None:
        return None
    try:
        block = format_preferences(get_preferences(owner_user_id))
    except PreferencesError:
        logger.warning("Could not load preferences for user %s; skipping", owner_user_id)
        return None
    return block or None


async def _history_with_preferences(
    formatted_history: list[dict], owner_user_id: int | None
) -> list[dict]:
    """Append the user's preference note to the history fed to the answer model."""
    note = await asyncio.to_thread(_preferences_note, owner_user_id)
    if not note:
        return formatted_history
    return [*formatted_history, {"role": "system", "content": note}]


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


async def _answer_policy_query(
    message: str,
    formatted_history: list[dict],
    allowed_tiers: list[str] | None,
    allowed_regions: list[str] | None = None,
) -> ChatResult:
    search_query = await _resolve_search_query(message, formatted_history)
    retrieved = retrieve_context(search_query, allowed_tiers=allowed_tiers, allowed_regions=allowed_regions)
    if retrieved.status == "blocked":
        contact = retrieved.blocked_contact or "HR"
        return ChatResult(CONFIDENTIAL_MESSAGE.format(contact=contact), "", [], status="blocked")
    if not retrieved.sources:
        return ChatResult(NO_RESULTS_MESSAGE, "", [], status="no_results")

    reply = await _run_blocking(
        get_llm_response,
        timeout_detail="The language model timed out while generating a response. Please try again.",
        user_message=message,
        context=retrieved.text,
        history=formatted_history,
    )

    # A refusal or "not enough info" reply isn't grounded in the retrieved
    # chunks, so drop the context and sources.
    if _is_refusal(reply):
        return ChatResult(reply, "", [], status="refused")
    if _is_insufficient_policy_answer(reply):
        return ChatResult(reply, "", [], status="no_results")
    return ChatResult(reply, retrieved.text, retrieved.sources, status="ok")


async def generate_chat_reply(
    session_id: str,
    message: str,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
) -> ChatResult:
    """Full chat flow: prepare history, classify, answer, persist the exchange.

    ``allowed_tiers`` is the RBAC gate: only documents in these access tiers are
    retrievable for this caller. The route derives it from the user's role.
    ``allowed_regions`` restricts retrieval to globally-visible docs and the
    caller's home region. The route derives it from the user's region claim.
    """
    if not message.strip():
        # Uniform envelope (matches the streaming path) so the client sees one
        # error shape everywhere.
        raise AppError("validation_error", "Message cannot be empty.", status_code=422)

    formatted_history = _prepare_history(session_id)

    # Classify before retrieval so meta and out-of-scope queries skip RAG.
    classification = await _run_blocking(
        classify_query,
        message,
        formatted_history,
        timeout_detail="The language model timed out while classifying the request. Please try again.",
    )

    if classification == "out_of_scope":
        result = ChatResult(REFUSAL_MESSAGE, "", [], status="refused")
    elif classification == "meta":
        # Preferences shape the answer, not the routing — inject only here.
        answer_history = await _history_with_preferences(formatted_history, owner_user_id)
        reply = await _run_blocking(
            get_meta_response,
            timeout_detail="The language model timed out while generating a response. Please try again.",
            user_message=message,
            history=answer_history,
        )
        result = ChatResult(reply, "", [], status="ok")
    else:
        answer_history = await _history_with_preferences(formatted_history, owner_user_id)
        result = await _answer_policy_query(message, answer_history, allowed_tiers, allowed_regions)

    try:
        # Mirror the streaming path: persist citations only for a grounded answer,
        # in the same Source shape the client receives.
        persisted_sources = _source_dicts(result.sources) if result.status == "ok" else []
        append_exchange(
            session_id, message, result.reply,
            owner_user_id=owner_user_id, sources=persisted_sources,
        )
    except ChatMemoryError as e:
        raise HTTPException(status_code=503, detail=e.message)

    return result


# --- SSE streaming ---

# Marks the end of the synchronous LLM token generator when pulled across threads.
_STREAM_DONE = object()


def _source_dicts(raw_sources: list[dict]) -> list[dict]:
    """Map the retriever's per-chunk dicts onto the frozen Source shape used in
    the response envelope (document_id/file/section/source_type)."""
    out = []
    for item in raw_sources:
        document_id = item.get("source", "")
        out.append({
            "document_id": document_id,
            "file": os.path.basename(document_id),
            "section": item.get("section"),
            "source_type": item.get("access_tier"),
        })
    return out


def _next_token(iterator):
    """Pull one token from a sync generator, returning the sentinel when drained.
    Called via ``asyncio.to_thread`` so the blocking LLM read never stalls the
    event loop while other requests are served."""
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_DONE


async def _persist_quietly(
    session_id: str,
    message: str,
    answer: str,
    owner_user_id: int | None = None,
    sources: list[dict] | None = None,
) -> None:
    """Persist the exchange after the answer is already streamed. A memory error
    here must not corrupt a response the client has fully received — log, don't raise.
    ``sources`` are the same Source-shaped citations the client received, stored so
    reopening the chat restores them."""
    try:
        await asyncio.to_thread(
            append_exchange, session_id, message, answer, owner_user_id, sources
        )
    except Exception:
        logger.exception("Failed to persist streamed exchange for session %s", session_id)


async def stream_chat_reply(
    session_id: str,
    message: str,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
):
    """Async generator yielding typed SSE events for the chat flow.

    Event protocol (clients MUST ignore unknown event types — this reserves
    ``tool_call``/``step`` for the later agentic path):
        token   {"delta": "..."}            incremental answer text
        sources {"sources": [Source, ...]}  citations (emitted once, after tokens)
        done    <ChatResponse envelope>     final answer/status/latency
        error   {"code","message","detail"} a failure; terminal

    Graceful non-answers (refused/blocked/no_results) emit a single ``done`` with
    that status and NO token events.
    """
    started = time.perf_counter()

    def _envelope(answer: str, sources: list[dict], status: str) -> dict:
        return {
            "answer": answer,
            "sources": sources,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "session_id": session_id,
            "status": status,
        }

    try:
        if not message.strip():
            yield {"event": "error", "data": {
                "code": "validation_error", "message": "Message cannot be empty.", "detail": None,
            }}
            return

        formatted_history = _prepare_history(session_id)
        classification = await asyncio.to_thread(classify_query, message, formatted_history)

        if classification == "out_of_scope":
            yield {"event": "done", "data": _envelope(REFUSAL_MESSAGE, [], "refused")}
            await _persist_quietly(session_id, message, REFUSAL_MESSAGE, owner_user_id)
            return

        answer_history = await _history_with_preferences(formatted_history, owner_user_id)

        if classification == "meta":
            answer = await asyncio.to_thread(get_meta_response, message, answer_history)
            yield {"event": "token", "data": {"delta": answer}}
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(answer, [], "ok")}
            await _persist_quietly(session_id, message, answer, owner_user_id)
            return

        # policy path
        search_query = await _resolve_search_query(message, formatted_history)
        retrieved = await asyncio.to_thread(
            retrieve_context, search_query,
            allowed_tiers=allowed_tiers, allowed_regions=allowed_regions,
        )

        if retrieved.status == "blocked":
            contact = retrieved.blocked_contact or "HR"
            answer = CONFIDENTIAL_MESSAGE.format(contact=contact)
            yield {"event": "done", "data": _envelope(answer, [], "blocked")}
            await _persist_quietly(session_id, message, answer, owner_user_id)
            return

        if not retrieved.sources:
            yield {"event": "done", "data": _envelope(NO_RESULTS_MESSAGE, [], "no_results")}
            await _persist_quietly(session_id, message, NO_RESULTS_MESSAGE, owner_user_id)
            return

        token_iter = stream_llm_response(message, retrieved.text, answer_history)
        full_answer = ""
        while True:
            delta = await asyncio.to_thread(_next_token, token_iter)
            if delta is _STREAM_DONE:
                break
            if delta:
                full_answer += delta
                yield {"event": "token", "data": {"delta": delta}}

        # Post-checks mirror the non-streaming path: an ungrounded answer drops
        # its sources and reports the matching status.
        final_sources: list[dict] = []
        if _is_refusal(full_answer):
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(full_answer, [], "refused")}
        elif _is_insufficient_policy_answer(full_answer):
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(full_answer, [], "no_results")}
        else:
            final_sources = _source_dicts(retrieved.sources)
            yield {"event": "sources", "data": {"sources": final_sources}}
            yield {"event": "done", "data": _envelope(full_answer, final_sources, "ok")}

        await _persist_quietly(session_id, message, full_answer, owner_user_id, final_sources)

    except Exception:
        logger.exception("Streaming chat failed for session %s", session_id)
        yield {"event": "error", "data": {
            "code": "internal_error", "message": "An unexpected error occurred.", "detail": None,
        }}
