import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.routes.chat import router as chat_router
from backend.core.chat_memory import initialize_chat_memory
from backend.core.rag import get_collection

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
    print("Initializing chat memory...")
    initialize_chat_memory()
    print("Chat memory ready.")

    chunk_count = get_collection().count()
    if chunk_count == 0:
        print(
            "WARNING: vector store is empty. Run the offline indexer before "
            "asking questions:\n    python -m backend.index_documents"
        )
    else:
        print(f"Vector store ready ({chunk_count} chunks).")

app.include_router(chat_router, prefix="/chat", tags=["Chat"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
