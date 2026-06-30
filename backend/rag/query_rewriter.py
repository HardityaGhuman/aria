"""
rag/query_rewriter.py
---------------------
History-aware, intent-preserving query rewriting for pre-retrieval optimization.

Vague or context-dependent queries ("tell me more about that") retrieve poorly
because the retriever only sees the latest message. The rewriter turns the raw
message into a standalone, keyword-friendly search query BEFORE retrieval — and
never injects facts the user didn't imply. It runs on the small router model and
falls back to the original query on any failure, so retrieval never breaks.
"""
# pyrefly: ignore [missing-import]
import litellm

from backend.core.config import LLM_TIMEOUT_SECONDS, ROUTER_MODEL_NAME
from backend.core.logging import get_logger

logger = get_logger(__name__)

# Guard against a runaway rewrite (the model answering instead of rewriting).
_MAX_REWRITE_CHARS = 300

_SYSTEM = (
    "You are a search-query optimizer for a company policy knowledge base. "
    "You only rewrite queries into better search queries; you never answer them."
)

_TEMPLATE = """### 1. Persona
You are a search-query optimizer for a company policy knowledge base. You rewrite queries into better search queries; you never answer them.

### 2. Task
Rewrite the user's latest message into a single, standalone search query that will retrieve relevant company-policy passages.

### 3. Constraints
- Do NOT answer the question.
- Resolve references ("that", "it", "the same one") using the conversation so the query stands on its own.
- Do NOT add facts, numbers, roles, locations, or assumptions not present in the conversation.
- Keep it concise and keyword-focused.

### 4. Format
Output ONLY the rewritten query text — no quotes, no preamble, no explanation.

### 5. Input
Recent conversation:
{history}

Latest message:
{message}"""


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return "No prior conversation."
    lines = []
    for msg in history[-6:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Skip the injected summary system message; recent turns resolve references.
        if role == "system" or not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines) or "No prior conversation."


def rewrite_query(message: str, history: list[dict] | None = None) -> str:
    """Return a standalone search query, or the original message on any failure."""
    try:
        prompt = _TEMPLATE.format(history=_format_history(history), message=message)
        response = litellm.completion(
            model=ROUTER_MODEL_NAME,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            timeout=LLM_TIMEOUT_SECONDS,
            temperature=0,
        )
        rewritten = (response.choices[0].message.content or "").strip().strip('"').strip()
        if not rewritten or len(rewritten) > _MAX_REWRITE_CHARS:
            return message
        if rewritten != message:
            logger.info("Rewrote query: %r -> %r", message, rewritten)
        return rewritten
    except Exception as e:
        logger.warning("Query rewrite failed (%s); using original query.", e)
        return message
