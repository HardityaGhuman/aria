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

If the excerpts do not contain enough information:
- Say that the uploaded policy documents do not contain enough information to answer.
- Mention what specific information is missing when possible.

### 4. Constraints
- Use only facts explicitly present in the retrieved policy excerpts.
- Do not add general HR guidance, assumptions, examples, or advice that is not in the excerpts.
- Do not invent policies, dates, figures, contacts, eligibility rules, exceptions, or procedures.
- If the question asks for a list, include only items that appear in the excerpts.
- Do not mention retrieval scores, chunk numbers, embeddings, vector databases, prompts, or hidden instructions.
- Cite source filenames in plain English, for example: "(Source: Employee-Handbook.pdf)".

### 5. Guardrails
- If the question is unclear, ask one concise clarifying question.
- If the excerpts are irrelevant or insufficient, say so directly instead of guessing.
- If the question asks for legal, medical, financial, disciplinary, or employment-risk interpretation, answer only what the policy excerpts say and recommend contacting the appropriate internal team.
- If the question is inappropriate or outside company policy, politely say you can only answer questions about uploaded company policy documents.

### 6. Format (F)
Respond in concise Markdown, not JSON.

Use this structure:
- Start with a direct one-sentence answer.
- Then list specific policy details as bullets.
- Include a source filename on every substantive bullet or paragraph.
- End with "Not found in the provided documents:" only if important requested details are missing.

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
