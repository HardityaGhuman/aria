# pyrefly: ignore [missing-import]
import litellm
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from backend.core.config import LLM_TIMEOUT_SECONDS, MODEL_NAME, ROUTER_MODEL_NAME, SYSTEM_PROMPT_PATH

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

Decide what the query is ABOUT, not what topic it mentions:
- meta: the query is about THIS CONVERSATION itself — the messages exchanged, what the user asked, what the assistant previously said, or a recap/count of the chat. The answer comes from the conversation transcript, not from policy documents. Signals: "I/you/we" referring to earlier turns, "this conversation/chat", "so far", "earlier", "last/previous question or answer", "what did you say", "repeat that", "how many questions", "recap/summarize our discussion".
- policy: the query asks for the SUBSTANCE of a company rule, benefit, procedure, entitlement, handbook topic, or operational policy. The answer comes from policy documents. This includes asking to summarize or explain a policy topic ("summarize the leave policy"), and follow-ups that ask for MORE policy detail ("tell me more about that", "what about sick leave") even though they reference the prior turn — those still need document content.
- out_of_scope: general knowledge, code, unrelated tasks, or any request to CREATE a new artifact (report, email, document, presentation, script, essay, policy draft). Content-generation is out_of_scope even when the topic is company policy.

Disambiguation rules:
- "summarize the <topic> policy" / "explain <topic>" -> policy (about document substance).
- "summarize/recap what WE discussed" or "what have I asked" -> meta (about the conversation).
- A follow-up that needs new policy facts -> policy, even if it says "you mentioned" or "earlier".
- A follow-up answerable purely from prior messages (e.g. "what was my first question", "repeat your last answer") -> meta.
- When a query mixes both, prefer meta only if it can be fully answered from the transcript without consulting documents.

Examples:
- "what benefits do employees get" -> policy
- "summarize the benefits policy" -> policy
- "tell me more about that" (after a policy answer) -> policy
- "what about parental leave?" -> policy
- "summarize what we discussed so far" -> meta
- "what was the first question I asked" -> meta
- "what did you just tell me about PTO" -> meta
- "how many questions have I asked" -> meta
- "can you repeat your previous answer" -> meta
- "list everything I've asked in order" -> meta
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
        model=ROUTER_MODEL_NAME,
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
    augmented_message = f"""Employee question:
{user_message}

Retrieved policy excerpts:
{context}"""
    
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
        messages = _build_messages(
            "You summarize internal policy assistant conversations.",
            summary_prompt,
        )
        response = litellm.completion(
            model=MODEL_NAME,
            messages=messages,
            timeout=LLM_TIMEOUT_SECONDS,
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"(Auto-summary fallback due to error: {str(e)}) " + (previous_summary or "")
