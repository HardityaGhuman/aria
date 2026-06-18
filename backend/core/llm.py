# pyrefly: ignore [missing-import]
import litellm
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
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


def _to_litellm_messages(messages: list[BaseMessage]) -> list[dict]:
    role_by_type = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
    }
    return [
        {"role": role_by_type.get(message.type, message.type), "content": message.content}
        for message in messages
    ]


def _history_to_messages(history: list[dict]) -> list[BaseMessage]:
    converted = []
    for message in history:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            converted.append(SystemMessage(content=content))
        elif role == "assistant":
            converted.append(AIMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def _build_messages(system_prompt: str, user_prompt: str, history: list[dict] | None = None) -> list[dict]:
    template = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{user_prompt}"),
        ]
    )
    formatted = template.format_messages(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    if history:
        formatted = [formatted[0], *_history_to_messages(history), formatted[1]]
    return _to_litellm_messages(formatted)


def classify_query(user_message: str, history: list[dict]) -> str:
    """Classify routing before retrieval so non-policy queries skip RAG."""
    history_excerpt = "\n".join(
        f"{msg.get('role', 'user')}: {msg.get('content', '')}"
        for msg in history[-6:]
        if msg.get("content")
    ) or "No prior conversation."
    classification_prompt = f"""Classify the user's latest query for a company policy assistant.

Return exactly one label and nothing else: policy, meta, or out_of_scope.

Definitions:
- policy: The user asks about a company rule, benefit, procedure, entitlement, handbook topic, or operational policy.
- meta: The user asks about this conversation, previous messages, what has been discussed, how many questions were asked, or asks for a recap of the chat.
- out_of_scope: The user asks for general knowledge, code, unrelated tasks, or asks the assistant to create a new artifact/report/email/document/presentation/script/essay/policy draft. Content-generation requests are out_of_scope even when the topic mentions company policy.

Examples:
- "what benefits do employees get" -> policy
- "summarize the benefits policy" -> policy
- "summarize what we discussed so far" -> meta
- "how many questions have I asked" -> meta
- "write me a report on employee benefits" -> out_of_scope
- "write an email explaining the leave policy" -> out_of_scope
- "what is the capital of France" -> out_of_scope

Recent conversation:
{history_excerpt}

Latest query:
{user_message}"""
    messages = _build_messages(
        "You are a precise routing classifier for a company policy assistant.",
        classification_prompt,
    )
    response = litellm.completion(
        model=MODEL_NAME,
        messages=messages,
        timeout=LLM_TIMEOUT_SECONDS,
        temperature=0,
    )
    label = response.choices[0].message.content.strip().lower()
    if "meta" in label:
        return "meta"
    if "out_of_scope" in label or "out of scope" in label:
        return "out_of_scope"
    return "policy"


def get_meta_response(user_message: str, history: list[dict]) -> str:
    """Answer conversation-history questions without retrieved policy context."""
    prompt = f"""The user is asking about this conversation, not about company policy documents.

Answer using only the conversation history provided in the messages. Do not cite policy documents, do not mention retrieved context, and do not add a "Not found in the provided documents" section.

User question:
{user_message}"""
    messages = _build_messages(
        "You answer questions about the current chat history only.",
        prompt,
        history,
    )
    response = litellm.completion(
        model=MODEL_NAME,
        messages=messages,
        timeout=LLM_TIMEOUT_SECONDS,
        temperature=0,
    )
    return response.choices[0].message.content.strip()

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

Question: {user_message}"""
    
    messages = _build_messages(system_prompt, augmented_message, history)

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
