from dotenv import load_dotenv
import json
import os

# Load .env from backend directory explicitly
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(env_path)

# Defaults follow Groq's migration off the deprecated llama-3.x endpoints
# (provider shutdown 2026-08-16): gpt-oss-120b answers, gpt-oss-20b routes.
MODEL_NAME = os.getenv("MODEL_NAME", "groq/openai/gpt-oss-120b")
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "groq/openai/gpt-oss-20b")
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

# --- Object storage (§11 slice C): private originals bucket ---
OBJECT_STORE_BACKEND = os.getenv("OBJECT_STORE_BACKEND", "s3").strip().lower()
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")   # empty = real AWS
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
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

# Deployment stage. Drives privacy-safe telemetry defaults: production omits the
# raw query text and pseudonymizes user/session ids unless explicitly overridden.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
_IS_PROD = APP_ENV == "production"
# Log the raw user query in request traces. Off by default in production (the
# query can carry PII); on in dev for debugging. Override with the env var.
TELEMETRY_LOG_RAW_QUERY = os.getenv(
    "TELEMETRY_LOG_RAW_QUERY", "false" if _IS_PROD else "true"
).strip().lower() in ("1", "true", "yes")
# Pseudonymize user/session ids in traces (salted HMAC, stable for correlation).
# On by default in production so raw identities never hit the log sink.
TELEMETRY_PSEUDONYMIZE_IDS = os.getenv(
    "TELEMETRY_PSEUDONYMIZE_IDS", "true" if _IS_PROD else "false"
).strip().lower() in ("1", "true", "yes")
# Salt for the id pseudonymization HMAC. Set a real secret in production so small
# integer user_ids aren't trivially re-identified by brute force.
TELEMETRY_ID_SALT = os.getenv("TELEMETRY_ID_SALT", "")

# --- Agentic tool layer (Phase A) ---
# Master rollback switch. false ⇒ the pipeline is pure-RAG, identical to today.
AGENT_TOOLS_ENABLED = os.getenv("AGENT_TOOLS_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Hard cap on agent-loop iterations — bounds cost/latency and a tool-call storm.
MAX_TOOL_STEPS = int(os.getenv("MAX_TOOL_STEPS", "3"))
# Model used to select/extract tool calls on the READ lane. Defaults to the small
# (20b) router model — a wrong read is self-scoped and harmless, so it need not ride
# the large model. The large model stays the default inside select_tool_call itself,
# so a future WRITE tool-select is unaffected (writes stay large). Final answer
# synthesis (get_llm_response) is always the large model.
AGENT_READ_MODEL = os.getenv("AGENT_READ_MODEL", ROUTER_MODEL_NAME)

# --- Write Slice 1: Slack Leave Self-Service (HITL-gated write) ---
# Master switch. false ⇒ leave routers unmounted, no write path exists.
LEAVE_AGENT_ENABLED = os.getenv("LEAVE_AGENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Slack OAuth app + inbound signature verification.
SLACK_CLIENT_ID = os.getenv("SLACK_CLIENT_ID", "")
SLACK_CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
# Shared bearer authenticating n8n -> our API.
N8N_SHARED_SECRET = os.getenv("N8N_SHARED_SECRET", "")
# Deterministic validator bounds.
LEAVE_MAX_CONSECUTIVE_DAYS = int(os.getenv("LEAVE_MAX_CONSECUTIVE_DAYS", "20"))
LEAVE_MIN_NOTICE_DAYS = int(os.getenv("LEAVE_MIN_NOTICE_DAYS", "3"))
# Comma-separated ISO dates (YYYY-MM-DD) that leave may not overlap. Empty = none.
LEAVE_BLACKOUT_DATES = [d.strip() for d in os.getenv("LEAVE_BLACKOUT_DATES", "").split(",") if d.strip()]
# Postgres store for LangGraph checkpoints. Defaults to the app DB.
LANGGRAPH_CHECKPOINT_DSN = os.getenv("LANGGRAPH_CHECKPOINT_DSN", DATABASE_URL)

# --- Write Slice 2: Jira work-request agent (HITL-gated write) ---
# Master switch. false ⇒ jira routers unmounted, no write path exists.
JIRA_AGENT_ENABLED = os.getenv("JIRA_AGENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
JIRA_ALLOWED_PROJECTS = [p.strip() for p in os.getenv(
    "JIRA_ALLOWED_PROJECTS", "MARKETING,DESIGN,FINANCE,IT,OFFICE").split(",") if p.strip()]
JIRA_ALLOWED_ISSUE_TYPES = [t.strip() for t in os.getenv(
    "JIRA_ALLOWED_ISSUE_TYPES", "Work Request,Access Request,Change Request,Task").split(",") if t.strip()]
JIRA_MAX_SUMMARY_LEN = int(os.getenv("JIRA_MAX_SUMMARY_LEN", "200"))
JIRA_MAX_DESCRIPTION_LEN = int(os.getenv("JIRA_MAX_DESCRIPTION_LEN", "4000"))


def _parse_approver_map(raw: str) -> dict:
    """JSON env → {project: approver_email}. Malformed/empty → {} (fail closed:
    an unmapped project routes to `unroutable`, never a silent default)."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


JIRA_PROJECT_APPROVERS = _parse_approver_map(os.getenv("JIRA_PROJECT_APPROVERS", ""))

# --- Write Slice 3: onboarding agent + write-boundary reliability ---------------
# Kill switch: off => routes are not mounted and openapi.json is unchanged.
ONBOARDING_AGENT_ENABLED = os.getenv("ONBOARDING_AGENT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
# Bounded retry budget at the write boundary. Transient failures only; a retried
# attempt is safe because every write tool is idempotent by case_id.
WRITE_MAX_ATTEMPTS = int(os.getenv("WRITE_MAX_ATTEMPTS", "3"))
# Consecutive transient failures before a connector's breaker opens.
WRITE_BREAKER_THRESHOLD = int(os.getenv("WRITE_BREAKER_THRESHOLD", "3"))

# --- Auth / JWT ---
# JWT_SECRET is validated at startup (see require_jwt_secret), NOT at import time,
# so tests and tooling can import config without a secret present. The server
# refuses to boot without it; signing tokens with a default key would be insecure.
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
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


DOCS_PATH = _resolve_backend_path(os.getenv("DOCS_PATH", os.path.join(DATA_DIR, "docs")))
SYSTEM_PROMPT_PATH = _resolve_backend_path(os.getenv("SYSTEM_PROMPT_PATH", os.path.join(os.path.dirname(BASE_DIR), "docs", "system_prompt.txt")))

if not MODEL_NAME:
    raise ValueError(
        "MODEL_NAME is missing. Please set it in your .env file.\n"
        "Format: provider/model_id (e.g. gemini/gemini-2.5-flash, openai/gpt-4o, groq/openai/gpt-oss-120b)"
    )

if not ROUTER_MODEL_NAME:
    raise ValueError(
        "ROUTER_MODEL_NAME is missing. Set it to a small model for classification, "
        "or omit it to reuse MODEL_NAME."
    )
