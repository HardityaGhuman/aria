from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routes.chat import router as chat_router
from backend.core.rag import initialize_vectorstore

app = FastAPI(
    title="Company Chatbot API",
    description="AI-powered chatbot backend using Gemini + RAG",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Load documents into ChromaDB on startup."""
    print("Initializing vector store...")
    initialize_vectorstore()
    print("Vector store ready.")

app.include_router(chat_router, prefix="/chat", tags=["Chat"])

@app.get("/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)