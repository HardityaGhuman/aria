"""
services/chat_service.py
------------------------
Application logic for the chat flow: conversation memory + summarisation, query
classification, retrieval-augmented answering, the grounding guardrails, and
answer scoring. Routes delegate here and stay thin.
"""
import asyncio
import os
import re
import time
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
from fastapi import HTTPException

from backend.core.chat_memory import (
    ChatMemoryError,
    SessionOwnershipError,
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
    RETRIEVAL_STRATEGY,
)
from backend.core.llm import (
    classify_query,
    count_tokens,
    get_chitchat_response,
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
from backend.core.trace import emit_request_trace, reset_trace, start_trace
from backend.core.tools.principal import Principal
from backend.rag import retrieve_context, rewrite_query
from backend.services.supervisor import route, run_specialist

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
_NO_RESULTS_BY_LANGUAGE = {
    "Spanish": (
        "No encontré información específica sobre eso en el manual. Por favor, "
        "consulta con RR. HH. o con la Dirección Ejecutiva."
    ),
    "French": (
        "Je n'ai pas trouvé d'information précise à ce sujet dans le manuel. "
        "Veuillez vous adresser aux RH ou à la Direction."
    ),
    "German": (
        "Dazu habe ich im Handbuch keine konkreten Informationen gefunden. Bitte "
        "wenden Sie sich an die Personalabteilung oder die Geschäftsleitung."
    ),
    "Hindi": (
        "मुझे इस बारे में पुस्तिका में कोई विशिष्ट जानकारी नहीं मिली। कृपया HR या "
        "कार्यकारी निदेशक से संपर्क करें।"
    ),
}

# A fixed, never-translated token the answer model emits when the retrieved
# context cannot answer the question at all. Detecting THIS instead of parsing
# the model's prose makes the ungrounded-answer guard language-agnostic — a
# translated "not enough information" sentence no longer slips past an
# English-only matcher and drags real sources onto an answer that used none.
NO_CONTEXT_SENTINEL = "__NO_CONTEXT_ANSWER__"


def _localized_no_results(language: str | None) -> str:
    """The 'couldn't find that' message in the user's language, English fallback."""
    return _NO_RESULTS_BY_LANGUAGE.get(language or "", NO_RESULTS_MESSAGE)


def _is_no_context_sentinel(reply: str) -> bool:
    """True when the model signalled it cannot answer from the retrieved context."""
    return reply.strip().startswith(NO_CONTEXT_SENTINEL)

# Pre-translated refusals for the languages the frontend offers (English variants
# normalize to "English"). The out-of-scope path returns instantly with no LLM
# call, so we cannot ask the model to translate — and would not want to spend a
# call on a fixed sentence. A language outside this set falls back to English.
_REFUSAL_BY_LANGUAGE = {
    "Spanish": (
        "Soy un asistente de políticas de la empresa, así que solo puedo ayudar "
        "con preguntas sobre las políticas y operaciones de la empresa. Por favor, "
        "haz una pregunta relacionada con las políticas."
    ),
    "French": (
        "Je suis un assistant dédié aux politiques de l'entreprise ; je ne peux "
        "donc répondre qu'aux questions portant sur les politiques et le "
        "fonctionnement de l'entreprise. Veuillez poser une question relative aux "
        "politiques."
    ),
    "German": (
        "Ich bin ein Assistent für Unternehmensrichtlinien und kann daher nur bei "
        "Fragen zu den Richtlinien und Abläufen des Unternehmens helfen. Bitte "
        "stellen Sie eine Frage zu den Richtlinien."
    ),
    "Hindi": (
        "मैं एक कंपनी नीति सहायक हूँ, इसलिए मैं केवल कंपनी की नीतियों और कार्यों से "
        "संबंधित प्रश्नों में ही मदद कर सकती हूँ। कृपया नीति से संबंधित प्रश्न पूछें।"
    ),
}


def _localized_refusal(language: str | None) -> str:
    """The out-of-scope refusal in the user's language, English as the fallback."""
    return _REFUSAL_BY_LANGUAGE.get(language or "", REFUSAL_MESSAGE)


CLARIFY_MESSAGE = (
    "I didn't quite catch that. Could you ask a specific policy question — for "
    "example about leave, benefits, expenses, equipment, or conduct?"
)
_CLARIFY_BY_LANGUAGE = {
    "Spanish": (
        "No entendí bien. ¿Podrías hacer una pregunta concreta sobre las políticas "
        "— por ejemplo sobre permisos, beneficios, gastos, equipos o conducta?"
    ),
    "French": (
        "Je n'ai pas bien compris. Pourriez-vous poser une question précise sur les "
        "politiques — par exemple sur les congés, les avantages, les frais, le "
        "matériel ou la conduite ?"
    ),
    "German": (
        "Das habe ich nicht ganz verstanden. Könnten Sie eine konkrete Frage zu den "
        "Richtlinien stellen — zum Beispiel zu Urlaub, Leistungen, Ausgaben, "
        "Ausstattung oder Verhalten?"
    ),
    "Hindi": (
        "मैं ठीक से समझ नहीं पाई। क्या आप किसी विशेष नीति के बारे में प्रश्न पूछ सकते हैं "
        "— जैसे छुट्टी, लाभ, खर्च, उपकरण या आचरण के बारे में?"
    ),
}

# Tokens that carry no answerable content on their own. A message made up only of
# these (or punctuation) is filler, not a question — it should prompt a
# clarification rather than be force-fit into a fabricated retrieval query.
_FILLER_TOKENS = {
    "um", "umm", "ummm", "uh", "uhh", "uhm", "hmm", "hm", "hmmm", "erm", "eh",
    "meh", "idk", "dunno", "huh", "ok", "okay", "k", "kk", "yeah", "ya", "yep",
    "yup", "oh", "ah", "so", "well", "uhh", "mm", "mmm",
}


def _localized_clarify(language: str | None) -> str:
    """The clarification prompt in the user's language, English as the fallback."""
    return _CLARIFY_BY_LANGUAGE.get(language or "", CLARIFY_MESSAGE)


def _is_low_content_message(message: str) -> bool:
    """True when the message is empty, punctuation-only, or made up entirely of
    filler tokens (``umm``, ``idk``, ``ok``) — i.e. carries no question to answer."""
    words = re.findall(r"[a-z]+", message.lower())
    if not words:
        return True  # empty or punctuation-only ("???", "  ")
    return all(word in _FILLER_TOKENS for word in words)


# Phrases that signal the user wants the PREVIOUS answer re-explained, not a new
# topic. Matched as substrings, plus a few bare one-word forms. Kept specific so a
# real question that merely contains "explain" ("explain the remote work policy")
# is not misread as a rephrase.
_REPHRASE_PATTERNS = (
    "dont get it", "don't get it", "do not get it", "didnt understand",
    "didn't understand", "not understand", "in simpler", "simpler terms",
    "simplify", "in other words", "rephrase", "reword", "explain it better",
    "explain better", "better terms", "one by one", "step by step",
    "explain again", "say it differently", "explain differently",
    "put it differently", "tell me more", "more detail", "copy paste",
    "copy pasted", "same words", "same thing again",
)
_REPHRASE_EXACT = {"elaborate", "explain", "go on", "continue", "more"}


def _is_rephrase_request(message: str) -> bool:
    """True when the message asks to re-explain the previous answer differently."""
    norm = message.lower().strip().strip("?.! ")
    if norm in _REPHRASE_EXACT:
        return True
    return any(pattern in norm for pattern in _REPHRASE_PATTERNS)


def _has_prior_answer(history: list[dict]) -> bool:
    """True when the conversation already has an assistant turn to re-explain."""
    return any(msg.get("role") == "assistant" for msg in history)


_REEXPLAIN_DIRECTIVE = (
    "The user did not understand your previous reply. Re-explain the SAME "
    "information more simply and in different words, broken into clear, separate "
    "points. Do not reuse your earlier phrasing or repeat the same sentences."
)
# Above 0 so a re-explain can't return the byte-identical text the user rejected,
# but low enough to stay grounded in the retrieved policy text.
_REEXPLAIN_TEMPERATURE = 0.4
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


def _grounding_status(reply: str) -> str:
    """Classify a policy-route reply: ok | no_results | refused.

    The single source of truth for the post-answer grounding check, shared by the
    sync and streaming paths so the two can't drift. An ungrounded reply (sentinel,
    refusal, whole-answer "not found") must never carry sources. The sentinel is
    the primary, language-agnostic signal; the English string matchers stay as a
    fallback for a model that answered in English without emitting the token."""
    if _is_no_context_sentinel(reply):
        return "no_results"
    if _is_refusal(reply):
        return "refused"
    if _is_insufficient_policy_answer(reply):
        return "no_results"
    return "ok"


def _rephrase_adjustments(message: str, formatted_history: list[dict]) -> tuple[str | None, float]:
    """(extra_directive, temperature) when the user asked to re-explain the
    previous answer, else (None, 0.0). At temperature 0 the same query retrieves
    the same chunks and the model returns byte-identical text — the directive plus
    a mild temperature bump breaks that."""
    if _is_rephrase_request(message) and _has_prior_answer(formatted_history):
        return _REEXPLAIN_DIRECTIVE, _REEXPLAIN_TEMPERATURE
    return None, 0.0


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


async def _prepare_history_async(session_id: str) -> list[dict]:
    """Run the synchronous history load/summarize/prune off the event loop.

    `_prepare_history` does blocking Postgres reads and, once a session crosses the
    token budget, a blocking LLM summarize call. Called bare from the async chat
    handlers it stalled the whole event loop for the request's duration, starving
    every other in-flight request. Offloading to a worker thread keeps the loop free.

    DEFERRED (tighten_plan 3.6, correctness-hardening not a blocker): serialize the
    summarize-and-prune per session so two concurrent turns on the SAME session
    can't both prune overlapping history. A naive per-session asyncio.Lock dict
    grows unbounded with session ids (its own small leak), so it's held back until
    the control layer owns a bounded session-scoped lock. The window is narrow
    (same user, two simultaneous turns) and non-security-affecting.
    """
    return await asyncio.to_thread(_prepare_history, session_id)


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


def _user_language(owner_user_id: int | None) -> str:
    """The user's stored language (normalized), or 'English'. Best-effort: a prefs
    DB hiccup must never break the no-LLM refusal/clarify paths that use it."""
    if owner_user_id is None:
        return "English"
    try:
        return get_preferences(owner_user_id).get("language") or "English"
    except PreferencesError:
        return "English"


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
    preferences: str | None = None,
    language: str = "English",
    principal: Principal | None = None,
    classification: str = "policy",
) -> ChatResult:
    search_query = await _resolve_search_query(message, formatted_history)
    retrieved = retrieve_context(search_query, allowed_tiers=allowed_tiers, allowed_regions=allowed_regions)
    if retrieved.status == "blocked":
        contact = retrieved.blocked_contact or "HR"
        return ChatResult(CONFIDENTIAL_MESSAGE.format(contact=contact), "", [], status="blocked")
    if not retrieved.sources:
        return ChatResult(_localized_no_results(language), "", [], status="no_results")

    extra_directive, temperature = _rephrase_adjustments(message, formatted_history)

    # Hierarchical routing: the classifier picked a domain; delegate to ONE specialist
    # over its scoped registry and fold its trusted tool note into the grounded answer.
    # Policy-agent / flag-off / gather failure ⇒ tool_note is None and the call is
    # byte-for-byte today's pure-RAG path.
    tool_note = None
    if principal is not None:
        specialist = route(classification, principal)
        spec_result = await run_specialist(specialist, message, formatted_history, principal)
        tool_note = spec_result.tool_note

    combined_directive = "\n\n".join(d for d in (tool_note, extra_directive) if d) or None

    reply = await _run_blocking(
        get_llm_response,
        timeout_detail="The language model timed out while generating a response. Please try again.",
        user_message=message,
        context=retrieved.text,
        history=formatted_history,
        preferences=preferences,
        extra_directive=combined_directive,
        temperature=temperature,
    )

    status = _grounding_status(reply)
    if status == "refused":
        return ChatResult(reply, "", [], status="refused")
    if status == "no_results":
        return ChatResult(_localized_no_results(language), "", [], status="no_results")
    return ChatResult(reply, retrieved.text, retrieved.sources, status="ok")


async def generate_chat_reply(
    session_id: str,
    message: str,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
    principal: Principal | None = None,
) -> ChatResult:
    """Full chat flow: prepare history, classify, answer, persist the exchange.

    ``allowed_tiers`` is the RBAC gate: only documents in these access tiers are
    retrievable for this caller. The route derives it from the user's role.
    ``allowed_regions`` restricts retrieval to globally-visible docs and the
    caller's home region. The route derives it from the user's region claim.
    """
    token = start_trace(owner_user_id, session_id)
    started = time.perf_counter()
    classification = ""
    result = None
    try:
        if not message.strip():
            # Uniform envelope (matches the streaming path) so the client sees one
            # error shape everywhere.
            raise AppError("validation_error", "Message cannot be empty.", status_code=422)

        formatted_history = await _prepare_history_async(session_id)

        # Bare filler ("umm", "idk") carries no question — clarify before spending a
        # classify call or letting the rewriter fabricate a search query from noise.
        if _is_low_content_message(message):
            classification = "clarify"
            language = await asyncio.to_thread(_user_language, owner_user_id)
            result = ChatResult(_localized_clarify(language), "", [], status="no_results")
            try:
                append_exchange(session_id, message, result.reply, owner_user_id=owner_user_id, sources=[])
            except ChatMemoryError as e:
                raise HTTPException(status_code=503, detail=e.message)
            return result

        # Classify before retrieval so meta and out-of-scope queries skip RAG.
        classification = await _run_blocking(
            classify_query,
            message,
            formatted_history,
            timeout_detail="The language model timed out while classifying the request. Please try again.",
        )

        if classification == "out_of_scope":
            language = await asyncio.to_thread(_user_language, owner_user_id)
            result = ChatResult(_localized_refusal(language), "", [], status="refused")
        elif classification == "chitchat":
            # Pass preferences (language) so a greeting honors the same language as
            # policy/meta answers — otherwise the language setting appears to apply at
            # random because only some routes respected it.
            pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
            reply = await _run_blocking(
                get_chitchat_response,
                message,
                pref_note,
                timeout_detail="The language model timed out. Please try again.",
            )
            result = ChatResult(reply, "", [], status="ok")
        elif classification == "meta":
            # Preferences shape the answer, not the routing — fold into the system
            # prompt (not history) so the length/tone/language directive carries weight.
            pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
            reply = await _run_blocking(
                get_meta_response,
                timeout_detail="The language model timed out while generating a response. Please try again.",
                user_message=message,
                history=formatted_history,
                preferences=pref_note,
            )
            result = ChatResult(reply, "", [], status="ok")
        else:
            pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
            language = await asyncio.to_thread(_user_language, owner_user_id)
            result = await _answer_policy_query(
                message, formatted_history, allowed_tiers, allowed_regions,
                preferences=pref_note, language=language, principal=principal,
                classification=classification,
            )

        try:
            # Mirror the streaming path: persist citations only for a grounded answer,
            # in the same Source shape the client receives.
            persisted_sources = to_source_dicts(result.sources) if result.status == "ok" else []
            append_exchange(
                session_id, message, result.reply,
                owner_user_id=owner_user_id, sources=persisted_sources,
            )
        except SessionOwnershipError as e:
            # Lost the race to claim a brand-new session_id another user just took.
            raise HTTPException(status_code=403, detail=e.message)
        except ChatMemoryError as e:
            raise HTTPException(status_code=503, detail=e.message)

        return result
    finally:
        emit_request_trace(
            query=message,
            classification=classification,
            strategy=RETRIEVAL_STRATEGY,
            retrieved=_retrieved_meta(result.sources) if result else [],
            status=result.status if result else "error",
            total_latency_ms=int((time.perf_counter() - started) * 1000),
        )
        reset_trace(token)


# --- SSE streaming ---

# Marks the end of the synchronous LLM token generator when pulled across threads.
_STREAM_DONE = object()


def _retrieved_meta(sources: list[dict]) -> list[dict]:
    """Doc id + similarity score per retrieved chunk, for the request rollup.
    Never includes document text — ids and scores only."""
    return [{"doc_id": s.get("source", ""), "score": s.get("distance")} for s in sources]


def to_source_dicts(raw_sources: list[dict]) -> list[dict]:
    """Map the retriever's per-chunk dicts onto the frozen Source shape used in
    the response envelope (document_id/file/section/source_type). Public: the
    route layer reuses it so the sync and SSE payloads share one mapping."""
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
    principal: Principal | None = None,
):
    """Async generator yielding typed SSE events for the chat flow.

    Event protocol (clients MUST ignore unknown event types — this reserves
    ``tool_call``/``step`` for the later agentic path):
        token   {"delta": "..."}            incremental answer text
        sources {"sources": [Source, ...]}  citations (emitted once, after tokens)
        done    <ChatResponse envelope>     final answer/status/latency
        error   {"error": {"code","message","detail"}}  a failure; terminal (matches REST envelope)

    Graceful non-answers (refused/blocked/no_results) emit a single ``done`` with
    that status and NO token events.
    """
    token = start_trace(owner_user_id, session_id)
    started = time.perf_counter()
    final_status = "error"
    classification = ""
    retrieved_meta: list[dict] = []

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

        formatted_history = await _prepare_history_async(session_id)

        # Bare filler ("umm", "idk") → clarify before classify/rewrite/retrieval.
        if _is_low_content_message(message):
            classification = "clarify"
            final_status = "no_results"
            language = await asyncio.to_thread(_user_language, owner_user_id)
            clarify = _localized_clarify(language)
            yield {"event": "token", "data": {"delta": clarify}}
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(clarify, [], "no_results")}
            await _persist_quietly(session_id, message, clarify, owner_user_id)
            return

        classification = await asyncio.to_thread(classify_query, message, formatted_history)

        if classification == "out_of_scope":
            final_status = "refused"
            language = await asyncio.to_thread(_user_language, owner_user_id)
            refusal = _localized_refusal(language)
            yield {"event": "token", "data": {"delta": refusal}}
            yield {"event": "done", "data": _envelope(refusal, [], "refused")}
            await _persist_quietly(session_id, message, refusal, owner_user_id)
            return

        if classification == "chitchat":
            pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
            answer = await asyncio.to_thread(get_chitchat_response, message, pref_note)
            final_status = "ok"
            yield {"event": "token", "data": {"delta": answer}}
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(answer, [], "ok")}
            await _persist_quietly(session_id, message, answer, owner_user_id)
            return

        # Preferences fold into the system prompt (see get_llm_response) rather
        # than a trailing history turn, so the directive isn't under-weighted.
        pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)

        if classification == "meta":
            answer = await asyncio.to_thread(get_meta_response, message, formatted_history, pref_note)
            final_status = "ok"
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
        retrieved_meta = _retrieved_meta(retrieved.sources)

        if retrieved.status == "blocked":
            final_status = "blocked"
            contact = retrieved.blocked_contact or "HR"
            answer = CONFIDENTIAL_MESSAGE.format(contact=contact)
            yield {"event": "done", "data": _envelope(answer, [], "blocked")}
            await _persist_quietly(session_id, message, answer, owner_user_id)
            return

        if not retrieved.sources:
            final_status = "no_results"
            language = await asyncio.to_thread(_user_language, owner_user_id)
            answer = _localized_no_results(language)
            yield {"event": "done", "data": _envelope(answer, [], "no_results")}
            await _persist_quietly(session_id, message, answer, owner_user_id)
            return

        # Hierarchical routing (streaming): delegate to ONE specialist, emit the
        # reserved tool events for whatever it gathered, then fold the trusted tool
        # note into the streamed grounded answer. Policy-agent / flag-off / gather
        # failure ⇒ tool_note is None, today's pure-RAG path.
        tool_note = None
        if principal is not None:
            specialist = route(classification, principal)
            spec_result = await run_specialist(specialist, message, formatted_history, principal)
            for entry in spec_result.tool_results:
                yield {"event": "tool_call", "data": {"name": entry["name"]}}
                yield {"event": "tool_result", "data": {
                    "name": entry["name"], "status": entry["result"].status}}
            tool_note = spec_result.tool_note

        extra_directive, temperature = _rephrase_adjustments(message, formatted_history)

        # Prepend the trusted tool note so the live number reaches the model.
        extra_directive = "\n\n".join(d for d in (tool_note, extra_directive) if d) or None

        token_iter = stream_llm_response(
            message, retrieved.text, formatted_history, pref_note,
            extra_directive=extra_directive, temperature=temperature,
        )
        # Gate the leading tokens: the model emits ONLY the no-context sentinel when
        # it can't answer, and we must not flash that raw token to the user. Buffer
        # until the text either completes the sentinel (suppress it) or diverges
        # from it (a real answer — flush the buffer and stream the rest live).
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

        # Post-checks share _grounding_status with the sync path. One transport
        # difference: if the whole reply was the sentinel it was never streamed, so
        # we can still replace it with the localized "couldn't find that" message;
        # any other ungrounded text has already reached the client and only its
        # status/sources can be corrected.
        final_sources: list[dict] = []
        if not gate_open and _is_no_context_sentinel(full_answer):
            final_status = "no_results"
            language = await asyncio.to_thread(_user_language, owner_user_id)
            full_answer = _localized_no_results(language)
            yield {"event": "token", "data": {"delta": full_answer}}
            yield {"event": "sources", "data": {"sources": []}}
            yield {"event": "done", "data": _envelope(full_answer, [], "no_results")}
        else:
            final_status = _grounding_status(full_answer)
            if final_status == "ok":
                final_sources = to_source_dicts(retrieved.sources)
            yield {"event": "sources", "data": {"sources": final_sources}}
            yield {"event": "done", "data": _envelope(full_answer, final_sources, final_status)}

        await _persist_quietly(session_id, message, full_answer, owner_user_id, final_sources)

    except Exception:
        logger.exception("Streaming chat failed for session %s", session_id)
        yield {"event": "error", "data": {"error": {
            "code": "internal_error", "message": "An unexpected error occurred.", "detail": None,
        }}}
    finally:
        emit_request_trace(
            query=message,
            classification=classification,
            strategy=RETRIEVAL_STRATEGY,
            retrieved=retrieved_meta,
            status=final_status,
            total_latency_ms=int((time.perf_counter() - started) * 1000),
        )
        reset_trace(token)
