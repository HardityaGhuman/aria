"""Per-response evaluation using an LLM-as-judge.

Scores a single grounded policy answer on three standard RAG dimensions, on a
1-5 scale, so the frontend can show live quality metrics per query. This is
deliberately lightweight: one extra model call per answer, strict-JSON output,
and robust parsing so a malformed judge reply never breaks the chat flow.
"""
import json
import re

# pyrefly: ignore [missing-import]
import litellm

from backend.core.config import LLM_TIMEOUT_SECONDS, MODEL_NAME

_JUDGE_SYSTEM = (
    "You are a precise evaluation judge for a company policy assistant. "
    "You output only a single compact JSON object and nothing else."
)

_JUDGE_TEMPLATE = """You are a STRICT evaluator. Score the assistant's ANSWER to the USER QUESTION using only the RETRIEVED CONTEXT. Be critical: reserve 5 for flawless cases and actively look for problems. When unsure, score lower.

Rate each metric as an integer from 1 to 5:
- faithfulness: is EVERY factual claim in the answer directly supported by the retrieved context? Deduct for any claim that is unsupported, embellished, or stated more confidently than the context warrants. 5 = every claim verifiable in the context; 3 = mostly supported but with an unsupported detail; 1 = fabricated or contradicts the context.
- answer_relevance: does the answer fully and directly address the question? Deduct for partial coverage, vagueness, padding, or missing parts of a multi-part question. 5 = complete and on-point; 3 = addresses the question but incomplete; 1 = off-topic or evasive.
- context_relevance: how much of the retrieved context is actually relevant and sufficient to answer the question? Deduct heavily when most retrieved chunks are unrelated to the question. 5 = tightly relevant and sufficient; 3 = relevant chunks mixed with noise; 1 = mostly unrelated.

Do not give the same score to every metric out of habit — judge each independently.

Respond with ONLY this JSON, no prose, no code fences:
{{"faithfulness": <1-5>, "answer_relevance": <1-5>, "context_relevance": <1-5>, "rationale": "<one short sentence naming the main weakness, or 'no issues found'>"}}

USER QUESTION:
{question}

RETRIEVED CONTEXT:
{context}

ASSISTANT ANSWER:
{answer}"""

# Keep the judge prompt bounded regardless of how much context was retrieved.
_MAX_CONTEXT_CHARS = 6000


def _clamp_score(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(1, min(5, int(round(value))))
    return None


def _parse_scores(raw: str) -> dict:
    """Parse the judge reply into scores, tolerating fences / stray prose."""
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    data = {}
    try:
        data = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = {}

    def score(key: str) -> int | None:
        parsed = _clamp_score(data.get(key))
        if parsed is not None:
            return parsed
        # Last resort: pull the first digit after the key name.
        fallback = re.search(rf'"{key}"\s*:\s*(\d)', raw)
        return int(fallback.group(1)) if fallback else None

    rationale = data.get("rationale")
    return {
        "faithfulness": score("faithfulness"),
        "answer_relevance": score("answer_relevance"),
        "context_relevance": score("context_relevance"),
        "rationale": (rationale if isinstance(rationale, str) else "")[:200],
    }


def evaluate_answer(question: str, answer: str, context: str) -> dict:
    """Judge one grounded answer. Returns {"evaluated": False} when there is no
    retrieved context to grade (off-topic refusals, meta replies, empty hits)."""
    if not (context and context.strip()):
        return {"evaluated": False}

    prompt = _JUDGE_TEMPLATE.format(
        question=question,
        context=context[:_MAX_CONTEXT_CHARS],
        answer=answer,
    )
    response = litellm.completion(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        timeout=LLM_TIMEOUT_SECONDS,
        temperature=0,
    )
    scores = _parse_scores(response.choices[0].message.content or "")
    scores["evaluated"] = True
    return scores
