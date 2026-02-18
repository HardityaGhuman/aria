import google.generativeai as genai
from backend.core.config import GEMINI_API_KEY, MODEL_NAME, SYSTEM_PROMPT_PATH

genai.configure(api_key=GEMINI_API_KEY)


def load_system_prompt() -> str:
    try:
        with open(SYSTEM_PROMPT_PATH, "r") as f:
            return f.read()
    except FileNotFoundError:
        return "You are a helpful company assistant."


def build_gemini_model():
    """Create a Gemini GenerativeModel with the system prompt loaded."""
    system_prompt = load_system_prompt()
    return genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=system_prompt
    )


def get_gemini_response(user_message: str, context: str, history: list[dict]) -> str:
    """
    Send a message to Gemini with RAG context injected.

    Args:
        user_message: The user's latest query.
        context: Retrieved document chunks from ChromaDB.
        history: List of {"role": "user"/"model", "parts": [text]} dicts.

    Returns:
        The model's response as a string.
    """
    model = build_gemini_model()

    # Inject RAG context into the user message
    augmented_message = f"""Use the following context to answer the question if relevant.

--- Context ---
{context}
---------------

Question: {user_message}"""

    chat = model.start_chat(history=history)
    response = chat.send_message(augmented_message)
    return response.text