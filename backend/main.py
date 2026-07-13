import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.routes.chat import router as chat_router
from backend.routes.auth import router as auth_router
from backend.routes.admin import router as admin_router
from backend.routes.me import router as me_router
from backend.core.preferences import initialize_preferences_table
from backend.core.chat_memory import initialize_chat_memory
from backend.core.config import FRONTEND_ORIGIN, JIRA_AGENT_ENABLED, LEAVE_AGENT_ENABLED, require_jwt_secret
from backend.core.doc_status import initialize_doc_status_table
from backend.core.errors import register_error_handlers
from backend.core.ratelimit import limiter, rate_limit_handler
# pyrefly: ignore [missing-import]
from slowapi.errors import RateLimitExceeded
from backend.core.tokens import initialize_tokens_table
from backend.core.users import initialize_users_table
from backend.core.logging import get_logger, setup_logging
from backend.rag import get_repository
from backend.rag.vector_schema import ensure_vector_extension, initialize_vector_store_schema

setup_logging()
logger = get_logger("company-chatbot")

app = FastAPI(
    title="Company Chatbot API",
    description="AI-powered chatbot backend using LiteLLM + RAG",
    version="1.0.0",
    redirect_slashes=False,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,  # required for the refresh cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# Uniform error envelope: {"error": {"code", "message", "detail"}}.
register_error_handlers(app)

# Rate limiting (slowapi). The limiter needs to live on app.state; a breach maps
# to the same error envelope (429 + Retry-After).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

@app.on_event("startup")
async def startup_event():
    """Prepare chat memory and verify the prebuilt vector index is present.

    Documents are indexed offline (see backend/index_documents.py); the server
    only reads the existing index and never ingests PDFs at runtime.
    """
    # Refuse to boot without a JWT secret — never sign tokens with a default key.
    require_jwt_secret()

    logger.info("Initializing chat memory and users table...")
    initialize_chat_memory()
    initialize_users_table()
    initialize_tokens_table()
    initialize_doc_status_table()
    initialize_preferences_table()
    logger.info("Chat memory, users, refresh-token, doc-status, and preferences tables ready.")

    # Authoritative vector store: create the pgvector extension (fail fast if the
    # binary is missing) + the documents/document_versions/chunks schema, then
    # report how many chunks are indexed.
    ensure_vector_extension()
    initialize_vector_store_schema()
    logger.info("Vector store schema ready.")

    # Private originals store: verify the S3 bucket is reachable + authorized so
    # a bad bucket/region/credential stops boot with a clear message (§11 slice C).
    from backend.core.config import OBJECT_STORE_BACKEND
    if OBJECT_STORE_BACKEND == "s3":
        from backend.rag.object_store import get_object_store
        get_object_store().ensure_bucket()
        logger.info("Originals object store ready (bucket reachable).")

    if LEAVE_AGENT_ENABLED:
        from backend.core.slack_identity import initialize_slack_identity_table
        from backend.core.leave_case import initialize_leave_case_tables
        from backend.services.leave_checkpointer import get_checkpointer
        from backend.services.leave_graph import build_leave_graph
        from backend.core.hris.mock import MockHRIS
        from backend.routes.leave_agent import set_graph
        initialize_slack_identity_table()
        initialize_leave_case_tables()
        graph = build_leave_graph(hris=MockHRIS(), checkpointer=get_checkpointer())
        set_graph(graph)
        logger.info("Leave write agent ready (LEAVE_AGENT_ENABLED=true).")

    if JIRA_AGENT_ENABLED:
        from backend.core.jira.mock import MockJira
        from backend.core.jira_case import initialize_jira_case_tables
        from backend.services.jira_graph import build_jira_graph
        from backend.services.leave_checkpointer import get_checkpointer
        from backend.routes.jira_agent import set_graph as set_jira_graph

        initialize_jira_case_tables()
        jira_graph = build_jira_graph(jira=MockJira(), checkpointer=get_checkpointer())
        set_jira_graph(jira_graph)
        logger.info("Jira write agent ready (JIRA_AGENT_ENABLED=true).")

    chunk_count = get_repository().count()
    if chunk_count == 0:
        logger.warning(
            "Vector store is empty. Run the offline indexer before asking "
            "questions: python -m backend.index_documents"
        )
    else:
        logger.info("Vector store ready (%d chunks).", chunk_count)


@app.on_event("shutdown")
async def shutdown_event():
    """Close the DB connection pool so its worker threads stop cleanly."""
    from backend.core.db import close_pool
    close_pool()
    # One process-wide checkpointer, shared by both write agents — close it if
    # EITHER opened it.
    if LEAVE_AGENT_ENABLED or JIRA_AGENT_ENABLED:
        from backend.services.leave_checkpointer import close_checkpointer
        close_checkpointer()

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(me_router)
app.include_router(chat_router, prefix="/chat", tags=["Chat"])

if LEAVE_AGENT_ENABLED:
    from backend.routes.slack_auth import router as slack_auth_router
    from backend.routes.leave_agent import router as leave_agent_router
    app.include_router(slack_auth_router)
    app.include_router(leave_agent_router)

if JIRA_AGENT_ENABLED:
    from backend.routes.jira_agent import router as jira_agent_router
    app.include_router(jira_agent_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
