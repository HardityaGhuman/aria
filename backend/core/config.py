from dotenv import load_dotenv
import os

# Load .env from backend directory explicitly
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

MODEL_NAME = os.getenv("MODEL_NAME", "gemini/gemini-3.5-flash")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/company_chatbot")
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDINGS_LOCAL_ONLY = os.getenv("EMBEDDINGS_LOCAL_ONLY", "true").lower() == "true"
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "5"))
RRF_K_CONSTANT = int(os.getenv("RRF_K_CONSTANT", "60"))
BM25_CANDIDATE_POOL = int(os.getenv("BM25_CANDIDATE_POOL", "20"))
EXPAND_SECTION_RETRIEVAL = os.getenv("EXPAND_SECTION_RETRIEVAL", "true").lower() == "true"
# Base directory is "backend/"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

def _resolve_backend_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(BASE_DIR, path))


CHROMA_DB_PATH = _resolve_backend_path(os.getenv("CHROMA_DB_PATH", os.path.join(DATA_DIR, "chroma_db")))
DOCS_PATH = _resolve_backend_path(os.getenv("DOCS_PATH", os.path.join(DATA_DIR, "docs")))
SYSTEM_PROMPT_PATH = _resolve_backend_path(os.getenv("SYSTEM_PROMPT_PATH", os.path.join(os.path.dirname(BASE_DIR), "docs", "system_prompt.txt")))

if not MODEL_NAME:
    raise ValueError(
        "MODEL_NAME is missing. Please set it in your .env file.\n"
        "Format: provider/model_id (e.g. gemini/gemini-2.5-flash, openai/gpt-4o, groq/llama-3.3-70b-versatile)"
    )
