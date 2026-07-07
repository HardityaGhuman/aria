"""
services/chat_service.py
------------------------
Transport-facing facade over the shared read pipeline (§6).

All the shared work — history + summarisation, classification, the typed
`ReadPlan`, query rewrite, retrieval, specialist gather, grounding — lives in
`services/read_pipeline.py` so the sync and streaming transports can't drift. This
module holds only what is genuinely transport-specific: how the answer reaches the
client. `generate_chat_reply` returns the whole answer; `stream_chat_reply` yields
SSE token events. Both consume the same `PreparedRead` and the same
`finalize_answer`, so their terminal status and sources are guaranteed to agree.

Shared helpers and the fixed user-facing messages are re-exported from the pipeline
so existing importers (routes, tests) keep resolving `chat_service.<name>`.
"""
import asyncio
import time
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
from fastapi import HTTPException

from backend.core.chat_memory import (
    ChatMemoryError,
    SessionOwnershipError,
    append_exchange,
)
from backend.core.control.models import (
    RequestOutcome,
    SourceRef,
    TerminalState,
    envelope_status_for,
)
from backend.core.errors import AppError
from backend.core.llm import get_llm_response, stream_llm_response
from backend.core.logging import get_logger
from backend.core.tools.principal import Principal
from backend.core.trace import reset_trace, start_trace

# Re-exported so `chat_service.<name>` keeps resolving for routes and tests. These are
# the shared pipeline's public surface: the source mapper the route reuses, the
# localized fixed messages, the sentinel, and the grounding helpers.
from backend.services.read_pipeline import (  # noqa: F401  (re-export)
    CLARIFY_MESSAGE,
    CONFIDENTIAL_MESSAGE,
    NO_CONTEXT_SENTINEL,
    NO_RESULTS_MESSAGE,
    REFUSAL_MESSAGE,
    _NO_RESULTS_BY_LANGUAGE,
    _grounding_status,
    _is_low_content_message,
    _is_no_context_sentinel,
    _is_refusal,
    _is_rephrase_request,
    _localized_clarify,
    _localized_no_results,
    _localized_refusal,
    _run_blocking,
    finalize_answer,
    prepare_read,
    to_source_dicts,
)

logger = get_logger(__name__)


@dataclass
class ChatResult:
    reply: str
    context_used: str
    sources: list[dict]
    # ok | partial | no_results | blocked | refused | tool_unavailable | error —
    # surfaced in the response envelope so the client branches on outcome, not prose.
    status: str = "ok"


def _source_refs(source_dicts: list[dict]) -> tuple[SourceRef, ...]:
    """Envelope-shaped source dicts → the frozen `SourceRef` tuple a `RequestOutcome`
    carries into its trace. Ids/labels only; no document body ever enters the trace."""
    return tuple(
        SourceRef(
            document_id=s["document_id"], file=s["file"],
            section=s.get("section"), source_type=s.get("source_type"),
        )
        for s in source_dicts
    )


# Terminal states whose grounded answer stands on retrieved evidence, so the reply
# carries context + sources. PARTIAL (policy-only when a tool was down) still cites the
# policy it used; every non-answer terminal carries none.
_GROUNDED_TERMINALS = (TerminalState.OK, TerminalState.PARTIAL)


async def _generate_grounded_answer(prepared) -> str:
    """Run the final answer model for the grounded (streamable) branch, sync. The
    streaming transport uses `stream_llm_response` instead; everything else about the
    call — message, context, history, preferences, directive, temperature — is the
    SAME `AnswerRequest` both read, so the two can't diverge on what they ask for."""
    ar = prepared.answer_req
    return await _run_blocking(
        get_llm_response,
        timeout_detail="The language model timed out while generating a response. Please try again.",
        user_message=ar.message,
        context=ar.context,
        history=ar.history,
        preferences=ar.preferences,
        extra_directive=ar.directive,
        temperature=ar.temperature,
    )


async def generate_chat_reply(
    session_id: str,
    message: str,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
    principal: Principal | None = None,
) -> ChatResult:
    """Full chat flow (sync): run the shared pipeline, generate the answer if the
    branch is grounded, persist the exchange, return a `ChatResult`.

    ``allowed_tiers``/``allowed_regions`` are the RBAC + region gate the route derives
    from the caller's JWT; the pipeline threads them into retrieval.
    """
    token = start_trace(owner_user_id, session_id)
    started = time.perf_counter()
    result: ChatResult | None = None
    prepared = None
    try:
        if not message.strip():
            # Uniform envelope (matches the streaming path) so the client sees one
            # error shape everywhere.
            raise AppError("validation_error", "Message cannot be empty.", status_code=422)

        prepared = await prepare_read(
            session_id, message, principal=principal,
            allowed_tiers=allowed_tiers, allowed_regions=allowed_regions,
            owner_user_id=owner_user_id,
        )

        # §6a: an error-terminal (INVALID_PLAN, INTERNAL_ERROR, etc.) can't ride the
        # ChatResponse status Literal (it only allows the 6 client-facing values), so
        # surface it as a structured AppError the route maps to a JSON error body.
        if not prepared.streamable:
            status = envelope_status_for(prepared.terminal)
            if status == "error":
                raise AppError(
                    "internal_error",
                    prepared.answer or "The request could not be processed.",
                    status_code=500,
                )
            result = ChatResult(
                prepared.answer, "", [],
                status=status,
            )
        else:
            if prepared.tracer:
                prepared.tracer.answer_started()
            ans_started = time.perf_counter()
            reply = await _generate_grounded_answer(prepared)
            if prepared.tracer:
                prepared.tracer.answer_completed(
                    latency_ms=int((time.perf_counter() - ans_started) * 1000),
                    source_count=len(prepared.sources)
                )
            final = finalize_answer(prepared, reply)
            grounded = final.terminal in _GROUNDED_TERMINALS
            # ChatResult.sources holds the RAW retriever dicts; the boundary (route +
            # persist + trace) maps them through `to_source_dicts` (which also drops
            # malformed §6c cites). Keeping raw here matches the streaming `done` event
            # once mapped, and the `_retrieved_meta` rollup which reads raw scores.
            result = ChatResult(
                final.answer,
                prepared.context if grounded else "",
                prepared.sources if grounded else [],
                status=envelope_status_for(final.terminal),
            )

        try:
            # Persist citations for a grounded answer (ok or a policy-only partial), in
            # the same Source shape the client receives.
            persisted_sources = (
                to_source_dicts(result.sources)
                if result.status in ("ok", "partial") else []
            )
            append_exchange(
                session_id, message, result.reply,
                owner_user_id=owner_user_id, sources=persisted_sources,
            )
            if prepared.tracer:
                prepared.tracer.exchange_persisted()
        except SessionOwnershipError as e:
            # Lost the race to claim a brand-new session_id another user just took.
            raise HTTPException(status_code=403, detail=e.message)
        except ChatMemoryError as e:
            raise HTTPException(status_code=503, detail=e.message)

        if prepared.tracer:
            term = final.terminal if prepared.streamable else prepared.terminal
            prepared.tracer.request_completed(RequestOutcome(
                terminal_state=term,
                answer=result.reply,
                sources=_source_refs(to_source_dicts(result.sources)),
                latency_ms=int((time.perf_counter() - started) * 1000),
            ))
        return result
    except AppError as e:
        if prepared and prepared.tracer:
            term = prepared.terminal if prepared.terminal else TerminalState.INTERNAL_ERROR
            prepared.tracer.request_failed(term, error_code=e.code)
        raise
    except HTTPException as e:
        if prepared and prepared.tracer:
            term = prepared.terminal if prepared.terminal else TerminalState.INTERNAL_ERROR
            prepared.tracer.request_failed(term, error_code=str(e.status_code))
        raise
    except Exception as e:
        if prepared and prepared.tracer:
            prepared.tracer.request_failed(TerminalState.INTERNAL_ERROR, error_code="unexpected_error")
        raise
    finally:
        reset_trace(token)


# --- SSE streaming ---

# Marks the end of the synchronous LLM token generator when pulled across threads.
_STREAM_DONE = object()


def _next_token(iterator):
    """Pull one token from a sync generator, returning the sentinel when drained.
    Called via ``asyncio.to_thread`` so the blocking LLM read never stalls the event
    loop while other requests are served."""
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
) -> bool:
    """Persist the exchange after the answer is already streamed. A memory error here
    must not corrupt a response the client has fully received — log, don't raise.
    Returns True if persisted successfully."""
    try:
        await asyncio.to_thread(
            append_exchange, session_id, message, answer, owner_user_id, sources
        )
        return True
    except Exception:
        logger.exception("Failed to persist streamed exchange for session %s", session_id)
        return False


async def stream_chat_reply(
    session_id: str,
    message: str,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
    principal: Principal | None = None,
):
    """Async generator yielding typed SSE events for the chat flow.

    Event protocol (clients MUST ignore unknown event types):
        token   {"delta": "..."}            incremental answer text
        sources {"sources": [Source, ...]}  citations (emitted once, after tokens)
        done    <ChatResponse envelope>     final answer/status/latency
        error   {"error": {"code","message","detail"}}  a failure; terminal

    Graceful non-answers (blocked / empty no_results) emit a single ``done`` with that
    status and NO token events; spoken fixed branches (refusal / clarify / chitchat /
    meta) emit the message as one token. The grounded path streams the answer live.
    """
    token = start_trace(owner_user_id, session_id)
    started = time.perf_counter()
    prepared = None

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
            yield {"event": "error", "data": {"error": {
                "code": "validation_error", "message": "Message cannot be empty.", "detail": None,
            }}}
            return

        prepared = await prepare_read(
            session_id, message, principal=principal,
            allowed_tiers=allowed_tiers, allowed_regions=allowed_regions,
            owner_user_id=owner_user_id,
        )

        # --- fixed-answer branches (no answer model) --------------------------
        if not prepared.streamable:
            final_status = envelope_status_for(prepared.terminal)
            # §6a: error-terminals (INVALID_PLAN, etc.) surface as an SSE error event,
            # not a done — they can't ride the ChatResponse status Literal.
            if final_status == "error":
                if prepared.tracer:
                    prepared.tracer.request_failed(prepared.terminal, error_code="internal_error")
                yield {"event": "error", "data": {"error": {
                    "code": "internal_error",
                    "message": prepared.answer or "The request could not be processed.",
                    "detail": None,
                }}}
                return
            answer = prepared.answer
            if prepared.sse_speaks:
                yield {"event": "token", "data": {"delta": answer}}
            if prepared.sse_emits_empty_sources:
                yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(answer, [], final_status)}
            persisted = await _persist_quietly(session_id, message, answer, owner_user_id)
            if prepared.tracer:
                if persisted:
                    prepared.tracer.exchange_persisted()
                prepared.tracer.request_completed(RequestOutcome(
                    terminal_state=prepared.terminal,
                    answer=answer,
                    sources=(),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ))
            return

        # --- grounded path: stream the answer ---------------------------------
        # Emit the reserved tool events for whatever the specialist gathered.
        for entry in prepared.tool_results:
            yield {"event": "tool_call", "data": {"name": entry["name"]}}
            yield {"event": "tool_result", "data": {
                "name": entry["name"], "status": entry["result"].status}}

        ar = prepared.answer_req
        if prepared.tracer:
            prepared.tracer.answer_started()
        ans_started = time.perf_counter()
        token_iter = stream_llm_response(
            ar.message, ar.context, ar.history, ar.preferences,
            extra_directive=ar.directive, temperature=ar.temperature,
        )
        # Gate the leading tokens: the model emits ONLY the no-context sentinel when it
        # can't answer, and we must not flash that raw token to the user. Buffer until
        # the text either completes the sentinel (suppress it) or diverges (a real
        # answer — flush the buffer and stream the rest live).
        full_answer = ""
        gate_open = False
        while True:
            delta = await asyncio.to_thread(_next_token, token_iter)
            if delta is _STREAM_DONE:
                break
            if not delta:
                continue
            full_answer += delta
            if gate_open:
                yield {"event": "token", "data": {"delta": delta}}
                continue
            stripped = full_answer.lstrip()
            if not stripped or NO_CONTEXT_SENTINEL.startswith(stripped):
                continue  # whitespace, or still a possible sentinel prefix — hold
            gate_open = True
            yield {"event": "token", "data": {"delta": full_answer}}

        if prepared.tracer:
            prepared.tracer.answer_completed(
                latency_ms=int((time.perf_counter() - ans_started) * 1000),
                source_count=len(prepared.sources)
            )

        final = finalize_answer(prepared, full_answer)
        final_status = envelope_status_for(final.terminal)

        if not gate_open and _is_no_context_sentinel(full_answer):
            # The sentinel was fully buffered and never streamed, so we can still
            # replace it with the localized "couldn't find that" message.
            yield {"event": "token", "data": {"delta": final.answer}}
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(final.answer, [], final_status)}
            persisted = await _persist_quietly(session_id, message, final.answer, owner_user_id, [])
            if prepared.tracer:
                if persisted:
                    prepared.tracer.exchange_persisted()
                prepared.tracer.request_completed(RequestOutcome(
                    terminal_state=final.terminal,
                    answer=final.answer,
                    sources=(),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ))
        else:
            # Any answer text already reached the client; only its status/sources can
            # be corrected now (an ungrounded reply gets empty sources via finalize).
            yield {"event": "sources", "data": {"sources": final.sources}}
            yield {"event": "done", "data": _envelope(full_answer, final.sources, final_status)}
            persisted = await _persist_quietly(session_id, message, full_answer, owner_user_id, final.sources)
            if prepared.tracer:
                if persisted:
                    prepared.tracer.exchange_persisted()
                prepared.tracer.request_completed(RequestOutcome(
                    terminal_state=final.terminal,
                    answer=full_answer,
                    sources=_source_refs(final.sources),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ))

    except Exception:
        logger.exception("Streaming chat failed for session %s", session_id)
        if prepared and prepared.tracer:
            prepared.tracer.request_failed(TerminalState.INTERNAL_ERROR, error_code="internal_error")
        yield {"event": "error", "data": {"error": {
            "code": "internal_error", "message": "An unexpected error occurred.", "detail": None,
        }}}
    finally:
        reset_trace(token)
