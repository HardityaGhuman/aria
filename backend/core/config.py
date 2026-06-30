from dotenv import load_dotenv
import os

# Load .env from backend directory explicitly
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

MODEL_NAME = os.getenv("MODEL_NAME", "groq/llama-3.3-70b-versatile")
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "groq/llama-3.1-8b-instant")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
# Provider tokens-per-minute budget, used to throttle batch evaluation runs.
LLM_TOKENS_PER_MINUTE = int(os.getenv("LLM_TOKENS_PER_MINUTE", "12000"))
# Resilience: retry transient provider errors with exponential backoff, and cap
# the retrieved-context tokens so a big context never trips the model's limit.
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "0.5"))
LLM_CONTEXT_TOKEN_BUDGET = int(os.getenv("LLM_CONTEXT_TOKEN_BUDGET", "6000"))
# Rate limits (slowapi syntax). Protect the backend + the LLM budget.
RATE_LIMIT_CHAT = os.getenv("RATE_LIMIT_CHAT", "30/minute")
RATE_LIMIT_LOGIN = os.getenv("RATE_LIMIT_LOGIN", "10/minute")
# HR-only admin mutations (upload/reindex). Expensive (full reindex), so capped
# even though the endpoints are already role-gated.
RATE_LIMIT_ADMIN = os.getenv("RATE_LIMIT_ADMIN", "10/minute")
# Hard cap on a single uploaded document. Without it, `await file.read()` buffers
# the whole upload in memory — an oversized file is a memory-exhaustion DoS.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))  # 10 MiB
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/company_chatbot")
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
EMBEDDINGS_LOCAL_ONLY = os.getenv("EMBEDDINGS_LOCAL_ONLY", "true").lower() == "true"
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "6"))
# Vector candidates beyond this cosine distance are dropped as non-matches.
# Tunable so the eval harness can calibrate it instead of hardcoding a magic
# number in the retrieval code.
RETRIEVAL_MAX_DISTANCE = float(os.getenv("RETRIEVAL_MAX_DISTANCE", "0.8"))
RRF_K_CONSTANT = int(os.getenv("RRF_K_CONSTANT", "60"))
BM25_CANDIDATE_POOL = int(os.getenv("BM25_CANDIDATE_POOL", "10"))
# Active retrieval strategy for the live chat flow: "vector", "bm25", or "hybrid".
RETRIEVAL_STRATEGY = os.getenv("RETRIEVAL_STRATEGY", "hybrid")
# Rewrite the user's query (history-aware) before retrieval to resolve follow-ups.
QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "true").lower() == "true"

# Per-call LLM tracing + per-request rollup (offline observability). Default on;
# set false to disable all telemetry emission (the request path is unchanged).
TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").strip().lower() in ("1", "true", "yes")

# --- Auth / JWT ---
# JWT_SECRET is validated at startup (see require_jwt_secret), NOT at import time,
# so tests and tooling can import config without a secret present. The server
# refuses to boot without it; signing tokens with a default key would be insecure.
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "8"))
# Short-lived access token (minutes) + long-lived refresh token (days). The SPA
# holds the access token in memory and silently refreshes via the httpOnly cookie.
ACCESS_TOKEN_TTL_MIN = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "30"))
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "14"))
# CORS + refresh-cookie. Locked to the React dev origin by default; override in prod.
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"


def require_jwt_secret() -> str:
    """Return JWT_SECRET or raise if unset. Called at startup and before signing."""
    if not JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is not set. Refusing to issue or verify tokens with a "
            "default key. Set JWT_SECRET in backend/.env to a long random string."
        )
    return JWT_SECRET
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

if not ROUTER_MODEL_NAME:
    raise ValueError(
        "ROUTER_MODEL_NAME is missing. Set it to a small model for classification, "
        "or omit it to reuse MODEL_NAME."
    )
