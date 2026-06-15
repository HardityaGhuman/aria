import sys
from pathlib import Path

# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.middleware.cors import CORSMiddleware

sys.path.append(str(Path(__file__).resolve().parents[1]))

from backend.routes.chat import router as chat_router
from backend.core.chat_memory import initialize_chat_memory
from backend.core.rag import initialize_vectorstore

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
    """Load documents into ChromaDB on startup."""
    print("Initializing chat memory...")
    initialize_chat_memory()
    print("Chat memory ready.")
    print("Initializing vector store...")
    initialize_vectorstore()
    print("Vector store ready.")

app.include_router(chat_router, prefix="/chat", tags=["Chat"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
