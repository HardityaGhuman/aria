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
from backend.core.chat_memory import initialize_chat_memory
from backend.core.config import require_jwt_secret
from backend.core.users import initialize_users_table
from backend.core.logging import get_logger, setup_logging
from backend.rag import get_collection

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
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    logger.info("Chat memory and users table ready.")

    chunk_count = get_collection().count()
    if chunk_count == 0:
        logger.warning(
            "Vector store is empty. Run the offline indexer before asking "
            "questions: python -m backend.index_documents"
        )
    else:
        logger.info("Vector store ready (%d chunks).", chunk_count)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(chat_router, prefix="/chat", tags=["Chat"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
