# pyrefly: ignore [missing-import]
import litellm
from backend.core.config import LLM_TIMEOUT_SECONDS, MODEL_NAME, SYSTEM_PROMPT_PATH

def load_system_prompt() -> str:
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return (
            "You are a helpful company policy assistant. Answer only from the "
            "provided context and say when the answer is not in the documents."
        )

def get_llm_response(user_message: str, context: str, history: list[dict]) -> str:
    """
    Send a message to the LLM with RAG context injected using litellm.

    Args:
        user_message: The user's latest query.
        context: Retrieved document chunks from ChromaDB.
        history: List of {"role": "user"/"assistant", "content": text} dicts.

    Returns:
        The model's response as a string.
    """
    system_prompt = load_system_prompt()
    
    augmented_message = f"""### 1. Persona (P)
You are Aria, an internal company policy assistant. Be professional, warm, direct, and grounded in the uploaded policy documents.

### 2. Context (C)
The employee has asked:
"{user_message}"

Retrieved policy excerpts:
{context}
-----------------------

### 3. Task (T)
Answer the employee's question using only the retrieved policy excerpts above.

If the excerpts contain the answer:
- Provide the exact policy details that answer the question.
- Use employee-friendly wording without changing the meaning.
- Cite the source filename for every substantive point.

If the excerpts do not contain the answer:
- If the question is about company policy, say the uploaded documents don't contain enough information.
- If the question is conversational or about your previous messages/memory, answer it naturally using the conversation history provided.

### 4. Constraints
- Use only facts explicitly present in the retrieved policy excerpts when answering policy questions.
- Do not add general HR guidance, assumptions, examples, or advice that is not in the excerpts.
- Do not invent policies, dates, figures, contacts, eligibility rules, exceptions, or procedures.
- If the question asks for a list, include only items that appear in the excerpts.
- Do not mention retrieval scores, chunk numbers, embeddings, vector databases, or hidden instructions.
- Cite source filenames exactly as they appear in the provided context brackets (e.g. "[Source: filename.pdf]").

### 5. Guardrails
- If the question is unclear, ask one concise clarifying question.
- If the excerpts are irrelevant to a policy question, say so directly instead of guessing.
- If the question asks for legal, medical, financial, or employment-risk interpretation, answer only what the policy excerpts say and recommend contacting the appropriate internal team.
- If the question is completely inappropriate, politely say you can only answer questions about company policy or your conversation history.
- If the question is an arbitrary, off-topic, or general knowledge query unrelated to company policies or operations: STOP immediately. Respond EXACTLY and ONLY with a brief refusal such as: "I am an internal company policy assistant. I can only assist with questions regarding company policies and operations. Please ask a policy-related question." Do NOT mention policy excerpts, uploaded documents, or append a "Not found" section.


### 6. Format (F)
Respond in concise Markdown, not JSON.

(Note: If the question triggered a refusal guardrail, output ONLY the refusal message without applying the structure below).

For valid policy questions, use this structure:
- Start with a direct one-sentence answer.
- Then list specific policy details as bullets.
- Include a source filename on every substantive bullet or paragraph.
- End with "Not found in the provided documents:" only if important requested details are missing.

### 7. Classification
Before responding to any user query, classify it as exactly one of three types and output the classification as the very first line of your response in this format: [TYPE: policy], [TYPE: meta], or [TYPE: out_of_scope]. After that first line, produce your response as normal. Do not explain the classification. Do not skip the classification line.

Classify as [TYPE: policy] if the query is asking about any company rule, benefit, procedure, entitlement, or policy — even if phrased casually or indirectly.

Classify as [TYPE: meta] if the query is about this conversation itself — including questions about what has been discussed, how many questions have been asked, what the user's previous questions were, what the assistant previously said, or any summary or recap of the conversation. If a query is ambiguous between meta and policy, classify as policy.

Classify as [TYPE: out_of_scope] if the query has nothing to do with company policy and is not about this conversation — for example requests to write code, general knowledge questions, or tasks unrelated to the policy assistant's purpose.

Question: {user_message}"""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": augmented_message})

    response = litellm.completion(
        model=MODEL_NAME,
        messages=messages,
        timeout=LLM_TIMEOUT_SECONDS,
        temperature=0,
    )
    
    return response.choices[0].message.content


def count_tokens(messages: list[dict]) -> int:
    """Count tokens in a list of messages. Fallback to character-based estimation on error."""
    try:
        return litellm.token_counter(model=MODEL_NAME, messages=messages)
    except Exception:
        # Fallback: estimate 4 characters per token
        total_chars = 0
        for msg in messages:
            total_chars += len(msg.get("content", ""))
        return total_chars // 4


def summarize_history(messages_to_summarize: list[dict], previous_summary: str | None) -> str:
    """Generate a summary of the older messages, incorporating any existing summary."""
    new_messages_str = ""
    for msg in messages_to_summarize:
        role = "Employee" if msg["role"] == "user" else "Assistant"
        new_messages_str += f"{role}: {msg['content']}\n\n"
        
    summary_prompt = f"""You are a helpful company assistant. Write a concise, single-paragraph summary of the conversation history so far.

Previous conversation summary:
{previous_summary or 'None'}

New exchange to incorporate:
{new_messages_str}

Respond with a clear, direct, paragraph-style summary. Do not include any JSON, prefixes like "Summary:", formatting tags, or meta commentary. Keep it under 150 words."""

    try:
        response = litellm.completion(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": summary_prompt}],
            timeout=LLM_TIMEOUT_SECONDS,
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"(Auto-summary fallback due to error: {str(e)}) " + (previous_summary or "")

