"""services/read_pipeline.py
--------------------------
§6 — the single shared read pipeline.

Both chat transports (sync `POST /chat` and SSE `POST /chat/stream`) used to carry
their own near-copy of the same flow, and the two drifted. This module owns that
flow ONCE: history → filler guard → classify → build/validate a typed `ReadPlan`
(the control layer, §4) → branch → (policy/hr/calendar) rewrite + retrieve +
specialist gather. It returns a typed `PreparedRead`.

The one thing it deliberately does NOT do is call the final answer model on the
grounded path — that stays transport-specific so streaming can stream tokens while
sync gets the whole string. `PreparedRead.streamable` marks that branch; the
transport runs the model, then both call the shared `finalize_answer` (slice 2) to
map the produced text onto a terminal state + sources. Every other branch
(refusal / clarify / chitchat / meta / blocked / no_results) is fully resolved here
with its `TerminalState` already set.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field

# pyrefly: ignore [missing-import]
from fastapi import HTTPException

from backend.core import trace
from backend.core.chat_memory import (
    ChatMemoryError,
    delete_messages_before_id,
    get_history_with_ids,
    get_session_summary,
    update_session_summary,
)
from backend.core.config import (
    CHAT_WRITE_ENABLED,
    LLM_TIMEOUT_SECONDS,
    MAX_HISTORY_TOKENS,
    QUERY_REWRITE_ENABLED,
    RETRIEVAL_STRATEGY,
)
from backend.core.control.models import (
    ReadPlan,
    RequestContext,
    StepResult,
    TerminalState,
)
from backend.core.control.policies import on_hris_transient_failure
from backend.core.control.tracing import BoundaryTracer
from backend.core.errors import AppError
from backend.core.llm import (
    classify_query,
    count_tokens,
    get_chitchat_response,
    get_meta_response,
    summarize_history,
)
from backend.core.logging import get_logger
from backend.core.preferences import (
    PreferencesError,
    format_preferences,
    get_preferences,
)
from backend.core.tools.principal import Principal
from backend.rag import retrieve_context, rewrite_query
from backend.services.read_planner import (
    WRITE_INTENTS,
    agent_for_intent,
    build_plan,
    validate_plan,
)
from backend.services.supervisor import list_specialists, route, run_specialist
from backend.services.write_chat import (
    ExtractionFailed,
    agent_available,
    card_from_filing,
    card_from_row,
    file_case,
    list_cases_for_approver,
    render_approvals,
    render_filing,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Fixed user-facing messages (localized). These come back on the no-LLM paths,
# so they carry pre-translated variants for the languages the frontend offers.
# ---------------------------------------------------------------------------
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

# A fixed, never-translated token the answer model emits when the retrieved context
# cannot answer at all. Detecting THIS (not the model's prose) makes the ungrounded
# guard language-agnostic.
NO_CONTEXT_SENTINEL = "__NO_CONTEXT_ANSWER__"

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
CONFIDENTIAL_MESSAGE = (
    "That information is restricted and isn't available at your access level. "
    "Please contact {contact} for details."
)


def _localized_no_results(language: str | None) -> str:
    return _NO_RESULTS_BY_LANGUAGE.get(language or "", NO_RESULTS_MESSAGE)


def _localized_refusal(language: str | None) -> str:
    return _REFUSAL_BY_LANGUAGE.get(language or "", REFUSAL_MESSAGE)


def _localized_clarify(language: str | None) -> str:
    return _CLARIFY_BY_LANGUAGE.get(language or "", CLARIFY_MESSAGE)


def _is_no_context_sentinel(reply: str) -> bool:
    return reply.strip().startswith(NO_CONTEXT_SENTINEL)


# ---------------------------------------------------------------------------
# Low-content filler / rephrase detection (pre-classify short-circuits).
# ---------------------------------------------------------------------------
_FILLER_TOKENS = {
    "um", "umm", "ummm", "uh", "uhh", "uhm", "hmm", "hm", "hmmm", "erm", "eh",
    "meh", "idk", "dunno", "huh", "ok", "okay", "k", "kk", "yeah", "ya", "yep",
    "yup", "oh", "ah", "so", "well", "uhh", "mm", "mmm",
}


def _is_low_content_message(message: str) -> bool:
    """True when the message is empty, punctuation-only, or entirely filler tokens."""
    words = re.findall(r"[a-z]+", message.lower())
    if not words:
        return True
    return all(word in _FILLER_TOKENS for word in words)


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
    norm = message.lower().strip().strip("?.! ")
    if norm in _REPHRASE_EXACT:
        return True
    return any(pattern in norm for pattern in _REPHRASE_PATTERNS)


def _has_prior_answer(history: list[dict]) -> bool:
    return any(msg.get("role") == "assistant" for msg in history)


_REEXPLAIN_DIRECTIVE = (
    "The user did not understand your previous reply. Re-explain the SAME "
    "information more simply and in different words, broken into clear, separate "
    "points. Do not reuse your earlier phrasing or repeat the same sentences."
)
_REEXPLAIN_TEMPERATURE = 0.4


def _rephrase_adjustments(message: str, formatted_history: list[dict]) -> tuple[str | None, float]:
    """(extra_directive, temperature) when the user asked to re-explain the previous
    answer, else (None, 0.0)."""
    if _is_rephrase_request(message) and _has_prior_answer(formatted_history):
        return _REEXPLAIN_DIRECTIVE, _REEXPLAIN_TEMPERATURE
    return None, 0.0


# ---------------------------------------------------------------------------
# Grounding classification (shared by both transports' finalize step).
# ---------------------------------------------------------------------------
_FULL_REFUSAL_MAX_CHARS = 320


def _is_refusal(reply: str) -> bool:
    return reply.strip().lower().startswith("i'm a company policy assistant")


def _is_insufficient_policy_answer(reply: str) -> bool:
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
    """Classify a policy-route reply: ok | no_results | refused. Single source of
    truth shared by the sync and streaming finalize steps so the two can't drift."""
    if _is_no_context_sentinel(reply):
        return "no_results"
    if _is_refusal(reply):
        return "refused"
    if _is_insufficient_policy_answer(reply):
        return "no_results"
    return "ok"


# ---------------------------------------------------------------------------
# Source mapping.
# ---------------------------------------------------------------------------
def to_source_dicts(raw_sources: list[dict]) -> list[dict]:
    """Map the retriever's per-chunk dicts onto the frozen Source shape used in the
    response envelope (document_id/file/section/source_type).

    §6c citation validation: a chunk with no `source` (empty document_id) is malformed
    — it can't be cited (nothing to name, nothing the viewer could open), so it is
    dropped here before it can ride an `ok` completion. This is the single mapping the
    envelope, persistence, and trace all pass through, so the drop is consistent."""
    out = []
    for item in raw_sources:
        document_id = item.get("source", "")
        if not document_id:
            continue
        out.append({
            "document_id": document_id,
            "file": os.path.basename(document_id),
            "section": item.get("section"),
            "source_type": item.get("access_tier"),
        })
    return out


def _retrieved_meta(sources: list[dict]) -> list[dict]:
    """Doc id + similarity score per retrieved chunk, for the request rollup. Never
    includes document text — ids and scores only."""
    return [{"doc_id": s.get("source", ""), "score": s.get("distance")} for s in sources]


# ---------------------------------------------------------------------------
# History / preferences / query rewrite (offloaded from the event loop).
# ---------------------------------------------------------------------------
def _prepare_history(session_id: str) -> list[dict]:
    """Load conversation history + summary, summarising and pruning older messages
    once the token budget is exceeded."""
    try:
        history = get_history_with_ids(session_id)
        summary = get_session_summary(session_id)

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
    """Run the synchronous history load/summarize/prune off the event loop."""
    return await asyncio.to_thread(_prepare_history, session_id)


def _preferences_note(owner_user_id: int | None) -> str | None:
    """Prompt block for a user's durable preferences, or None. Best-effort."""
    if owner_user_id is None:
        return None
    try:
        block = format_preferences(get_preferences(owner_user_id))
    except PreferencesError:
        logger.warning("Could not load preferences for user %s; skipping", owner_user_id)
        return None
    return block or None


def _user_language(owner_user_id: int | None) -> str:
    """The user's stored language (normalized), or 'English'. Best-effort."""
    if owner_user_id is None:
        return "English"
    try:
        return get_preferences(owner_user_id).get("language") or "English"
    except PreferencesError:
        return "English"


async def _resolve_search_query(message: str, formatted_history: list[dict]) -> str:
    """Rewrite the message into a standalone search query (history-aware). Falls back
    to the original on any failure."""
    if not QUERY_REWRITE_ENABLED:
        return message
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(rewrite_query, message, formatted_history),
            timeout=LLM_TIMEOUT_SECONDS + 5,
        )
    except Exception:
        return message


async def _run_blocking(fn, *args, timeout_detail: str, **kwargs):
    """Offload a blocking LLM call to a worker thread with a timeout, mapping failures
    to the uniform error envelope."""
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


# ---------------------------------------------------------------------------
# The prepared read — the typed handoff from shared work to transport delivery.
# ---------------------------------------------------------------------------
@dataclass
class AnswerRequest:
    """Everything the answer model needs on the grounded (streamable) path. Bundled so
    the sync transport calls `get_llm_response(**)` and the SSE transport calls
    `stream_llm_response(**)` from one place, with no drift in what they pass."""
    message: str
    context: str
    history: list[dict]
    preferences: str | None
    directive: str | None
    temperature: float


@dataclass
class PreparedRead:
    """The result of all shared read work.

    Non-streamable branches are fully resolved: `terminal` is set, `answer` holds the
    delivered text, `streamable` is False. The grounded branch leaves `terminal=None`
    and `answer=None` with `streamable=True` and an `answer_req` — the transport runs
    the model and both call `finalize_answer` to settle the terminal + sources.
    `sources` are the raw retriever dicts (mapped to the envelope shape at delivery).
    """
    classification: str
    plan: ReadPlan
    streamable: bool
    terminal: TerminalState | None = None
    answer: str | None = None
    context: str = ""
    sources: list[dict] = field(default_factory=list)
    answer_req: AnswerRequest | None = None
    tool_results: list[dict] = field(default_factory=list)
    language: str = "English"
    # Write Cases this turn produced (a filing) or is asking a decision on (the approvals
    # lane). Empty on every read turn, so the envelope is unchanged for read traffic.
    cases: list[dict] = field(default_factory=list)
    # §6a: the per-request boundary tracer, created in prepare_read. The transports
    # reuse it to emit the lifecycle-end events (answer_completed / request_completed /
    # request_failed / exchange_persisted) they alone know the timing of.
    tracer: BoundaryTracer | None = None
    # §6b: an HR request whose live gather failed but that still has policy evidence.
    # The answer streams from policy alone; finalize settles it to PARTIAL, never a
    # silent OK, so the client learns the balance couldn't be read.
    forced_partial: bool = False

    @property
    def is_terminal(self) -> bool:
        """True when this branch is fully resolved and no answer model runs."""
        return not self.streamable

    @property
    def sse_speaks(self) -> bool:
        """Whether a fixed-answer branch should be streamed as a token event. The
        spoken branches are the ones that hand the user a written message —
        out_of_scope refusal, chitchat, meta, and the filler clarification. Blocked and
        empty-retrieval no_results stay silent (a lone `done` event), preserving the
        original SSE shape where a graceful non-answer carries no token."""
        return self.is_terminal and self.classification in {
            "out_of_scope", "chitchat", "meta", "clarify",
        }

    @property
    def sse_emits_empty_sources(self) -> bool:
        """Whether a spoken fixed branch also emits an empty `sources` event (so the
        client clears its source panel). Every spoken branch does EXCEPT the
        out_of_scope refusal, which historically emitted none."""
        return self.sse_speaks and self.classification != "out_of_scope"


@dataclass
class FinalizedAnswer:
    """The settled outcome of the grounded path after the answer model ran. `answer`
    is the text to deliver, `sources` are already in the envelope shape."""
    terminal: TerminalState
    answer: str
    sources: list[dict]


def finalize_answer(prepared: PreparedRead, full_answer: str) -> FinalizedAnswer:
    """Map a grounded answer string onto a terminal state + delivered answer +
    envelope sources. Shared by both transports so grounding can't drift between them.

    An ungrounded reply (the no-context sentinel, an off-topic refusal, or a whole-
    answer "not found") must never carry sources. The sentinel/insufficient cases are
    replaced with the localized "couldn't find that" message; a refusal is delivered
    as-is (it is already a complete sentence) but stripped of misleading sources."""
    status = _grounding_status(full_answer)
    if status == "no_results":
        return FinalizedAnswer(
            TerminalState.NO_RESULTS, _localized_no_results(prepared.language), [])
    if status == "refused":
        return FinalizedAnswer(TerminalState.REFUSED, full_answer, [])
    # A grounded reply. §6b: if the live gather failed on an HR request, the model
    # answered from policy alone — a real but incomplete answer → PARTIAL, still citing
    # the policy evidence it stands on. Otherwise a normal OK.
    sources = to_source_dicts(prepared.sources)
    if prepared.forced_partial:
        return FinalizedAnswer(TerminalState.PARTIAL, full_answer, sources)
    return FinalizedAnswer(TerminalState.OK, full_answer, sources)


def _write_lane_or_read(classification: str) -> str:
    """Keep a write lane only if this process can honour it; otherwise fall back to the
    read table. Two ways a lane dies here: the master switch is off (the model was never
    even offered the write labels, so this is belt-and-braces), or the agent it routes to
    is not registered (kill switch off / not built). Either way the user gets today's
    grounded answer instead of a promise nothing can keep."""
    if classification not in WRITE_INTENTS:
        return classification
    if not CHAT_WRITE_ENABLED:
        return "policy"
    agent = agent_for_intent(classification)
    if agent is not None and not agent_available(agent):
        return "policy"
    return classification


async def _prepare_write(classification: str, message: str, principal: Principal,
                         plan: ReadPlan, tracer: BoundaryTracer) -> PreparedRead:
    """File a Case, or list the ones awaiting this caller's decision.

    Everything a write lane can go wrong with degrades to a spoken failure, never a 500:
    the model may fail to extract usable fields, and the connector/DB may be down. The
    Case, if it exists at all, is the source of truth for what happened — this function
    only reports it."""
    if classification == "approvals":
        rows = await asyncio.to_thread(list_cases_for_approver, principal.email)
        cards = [card_from_row(row, principal.email) for row in rows]
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.OK, answer=render_approvals(cards),
            cases=cards, tracer=tracer,
        )

    agent = agent_for_intent(classification)
    try:
        filing = await asyncio.to_thread(file_case, agent, principal, message)
    except ExtractionFailed:
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.NO_RESULTS, tracer=tracer,
            answer=("I couldn't work out the details of that request, so I haven't filed "
                    "anything. Could you say it again with the specifics?"),
        )
    except Exception:                     # a connector or the DB is down — say so plainly
        logger.exception("write lane %s failed to file a case", classification)
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.NO_RESULTS, tracer=tracer,
            answer=("I couldn't file that request just now — the system behind it didn't "
                    "respond. Nothing was written. Please try again in a moment."),
        )

    card = card_from_filing(filing, principal.email)
    return PreparedRead(
        classification=classification, plan=plan, streamable=False,
        terminal=TerminalState.OK, answer=render_filing(filing), cases=[card],
        tracer=tracer,
    )


async def prepare_read(
    session_id: str,
    message: str,
    *,
    principal: Principal | None = None,
    allowed_tiers: list[str] | None = None,
    allowed_regions: list[str] | None = None,
    owner_user_id: int | None = None,
) -> PreparedRead:
    """Run every shared step and return a typed `PreparedRead`. Assumes `message` is
    non-empty (the transport rejects an empty body with a uniform validation error
    before calling). Never runs the final grounded answer model."""
    # §6a: one boundary tracer per request. It reads the ambient trace_id the transport
    # set with start_trace, so it needs no threading. `request_received` requires a
    # typed Principal (RequestContext), so it only fires on an authenticated call.
    tracer = BoundaryTracer()
    if principal is not None:
        ctx = trace.current_trace()
        tracer.request_received(RequestContext(
            trace_id=ctx.trace_id if ctx else "",
            principal=principal,
            session_id=session_id,
            message=message,
            intent="",  # not yet classified — this is the first boundary
            allowed_tiers=tuple(allowed_tiers or ()),
            allowed_regions=tuple(allowed_regions or ()),
        ))

    formatted_history = await _prepare_history_async(session_id)

    # Bare filler ("umm", "idk") carries no question — clarify before spending a
    # classify call or letting the rewriter fabricate a search query from noise. This
    # is a no-model fixed reply, same capability shape as an out_of_scope refusal.
    if _is_low_content_message(message):
        language = await asyncio.to_thread(_user_language, owner_user_id)
        return PreparedRead(
            classification="clarify",
            plan=build_plan("out_of_scope"),
            streamable=False,
            terminal=TerminalState.NO_RESULTS,
            answer=_localized_clarify(language),
            language=language,
            tracer=tracer,
        )

    classification = await _run_blocking(
        classify_query,
        message,
        formatted_history,
        timeout_detail="The language model timed out while classifying the request. Please try again.",
    )

    # A write lane only survives if this process can actually honour it. Otherwise it
    # normalizes back to a read lane BEFORE the plan is built, so a disabled (or missing)
    # write agent degrades to today's grounded answer instead of erroring the chat.
    classification = _write_lane_or_read(classification)

    tracer.intent_classified(classification)
    plan = build_plan(classification)
    tracer.plan_built(plan)

    # §6a: validate the typed plan BEFORE anything executes — but only on an
    # authenticated call, since validation needs a Principal to check specialist/tool
    # reachability. An invalid plan terminates the request in `invalid_plan` (which the
    # transports surface as an opaque error, never a ChatResponse status).
    if principal is not None:
        outcome = validate_plan(plan, principal, list_specialists())
        tracer.plan_validated(outcome)
        if not outcome.valid:
            return PreparedRead(
                classification=classification, plan=plan, streamable=False,
                terminal=TerminalState.INVALID_PLAN, answer=None, tracer=tracer,
            )

    if classification == "out_of_scope":
        language = await asyncio.to_thread(_user_language, owner_user_id)
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.REFUSED, answer=_localized_refusal(language),
            language=language, tracer=tracer,
        )

    # --- the write lanes. The reply is the Case, so there is nothing to stream and no
    # answer model to run: a pending write's state is a fact, and paraphrasing facts
    # through a model is how a system starts lying about what it did.
    if classification in WRITE_INTENTS and principal is not None:
        return await _prepare_write(classification, message, principal, plan, tracer)

    if classification == "chitchat":
        # Precompute — both transports deliver it whole (SSE as a single token event).
        pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
        reply = await _run_blocking(
            get_chitchat_response, message, pref_note,
            timeout_detail="The language model timed out. Please try again.",
        )
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.OK, answer=reply, tracer=tracer,
        )

    if classification == "meta":
        pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
        reply = await _run_blocking(
            get_meta_response,
            timeout_detail="The language model timed out while generating a response. Please try again.",
            user_message=message, history=formatted_history, preferences=pref_note,
        )
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.OK, answer=reply, tracer=tracer,
        )

    # --- grounded path: policy / hr / calendar --------------------------------
    tracer.specialist_selected(plan.specialist)
    pref_note = await asyncio.to_thread(_preferences_note, owner_user_id)
    language = await asyncio.to_thread(_user_language, owner_user_id)

    search_query = await _resolve_search_query(message, formatted_history)
    tracer.retrieval_started(RETRIEVAL_STRATEGY)
    retr_started = time.perf_counter()
    retrieved = await asyncio.to_thread(
        retrieve_context, search_query,
        allowed_tiers=allowed_tiers, allowed_regions=allowed_regions,
    )
    tracer.retrieval_completed(StepResult(
        kind="retrieval", name=RETRIEVAL_STRATEGY,
        status=getattr(retrieved, "status", "ok") or "ok",
        latency_ms=int((time.perf_counter() - retr_started) * 1000),
        meta={"count": len(retrieved.sources)},
    ))

    if retrieved.status == "blocked":
        contact = retrieved.blocked_contact or "HR"
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.BLOCKED,
            answer=CONFIDENTIAL_MESSAGE.format(contact=contact), language=language,
            tracer=tracer,
        )

    if not retrieved.sources:
        return PreparedRead(
            classification=classification, plan=plan, streamable=False,
            terminal=TerminalState.NO_RESULTS,
            answer=_localized_no_results(language), language=language,
            tracer=tracer,
        )

    # Hierarchical routing: delegate to ONE specialist over its scoped registry and
    # fold its trusted tool note into the answer. Policy-agent / flag-off / gather
    # failure ⇒ tool_note is None and the call is today's pure-RAG path.
    tool_note = None
    tool_results: list[dict] = []
    forced_partial = False
    if principal is not None:
        specialist = route(classification, principal)
        spec_result = await run_specialist(specialist, message, formatted_history, principal)
        tool_note = spec_result.tool_note
        tool_results = spec_result.tool_results
        for entry in tool_results:
            tracer.tool_selected(entry["name"])
            res = entry.get("result")
            tracer.tool_completed(StepResult(
                kind="tool", name=entry["name"],
                status=getattr(res, "status", "unknown") or "unknown", latency_ms=0,
            ))

        # §6b: an HR request whose live gather failed still has policy evidence here
        # (we returned no_results above if it didn't). Deterministic policy: the single
        # retry is spent, so degrade to a policy-only PARTIAL rather than a silent OK.
        # tool_note stays None, so the answer never asserts a live balance.
        if classification == "hr" and spec_result.status == "gather_failed":
            decision = on_hris_transient_failure(attempt=1, can_answer_from_policy=True)
            forced_partial = decision.terminal is TerminalState.PARTIAL

    extra_directive, temperature = _rephrase_adjustments(message, formatted_history)
    combined_directive = "\n\n".join(d for d in (tool_note, extra_directive) if d) or None

    return PreparedRead(
        classification=classification, plan=plan, streamable=True,
        terminal=None, answer=None, context=retrieved.text,
        sources=retrieved.sources, language=language, tool_results=tool_results,
        answer_req=AnswerRequest(
            message=message, context=retrieved.text, history=formatted_history,
            preferences=pref_note, directive=combined_directive, temperature=temperature,
        ),
        tracer=tracer, forced_partial=forced_partial,
    )
