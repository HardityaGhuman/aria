# Internal Company Chatbot — Gemini + RAG + Streamlit

A simple, AI-powered internal company assistant built with a modern Python stack.

---

## Stack

-   **LLM:** Gemini 2.5 Flash (`gemini-2.5-flash`)
-   **Backend:** FastAPI (Port 8000)
-   **Vector DB:** ChromaDB (Local, persists to `backend/data/chroma_db/`)
-   **Embeddings:** Sentence Transformers (`all-MiniLM-L6-v2`)
-   **Frontend:** Streamlit (Port 8501)
-   **Tools:** `python-dotenv`, `pydantic`, `uvicorn`, `requests`

---

## 📁 Structure

```
company-chatbot/
├── backend/
│   ├── main.py              # FastAPI app, triggers indexing on startup
│   ├── .env                 # GEMINI_API_KEY, MODEL_NAME, paths
│   ├── core/
│   │   ├── config.py        # Environment configuration
│   │   ├── gemini.py        # Gemini client & system prompt loading
│   │   └── rag.py           # RAG logic: ingestion, retrieval, chunking
│   ├── routes/
│   │   └── chat.py          # API Endpoints
│   └── data/
│       └── docs/            # DROP YOUR .TXT FILES HERE
├── frontend/
│   └── app.py               # Streamlit Chat UI
├── docs/
│   └── system_prompt.txt    # Bot persona & rules
└── requirements.txt
```

---

## How It Works

1.  **Startup:** `backend/main.py` scans `backend/data/docs/*.txt`.
2.  **Ingestion:** Files are chunked (split by paragraphs) and embedded into ChromaDB locally.
3.  **Chat Loop:**
    -   User sends message via Streamlit.
    -   FastAPI backend retrieves top 3 relevant chunks from ChromaDB.
    -   System prompt (persona) + Retrieved Chunks + User Message are sent to Gemini 2.5 Flash.
    -   Gemini generates a grounded response.

---

## Getting Started Locally

### 1. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment
Create `backend/.env`:
```ini
GEMINI_API_KEY=your_key_here
MODEL_NAME=gemini-2.5-flash
```

### 3. Run Backend (Terminal 1)
```bash
uvicorn backend.main:app --reload --port 8000
```

### 4. Run Frontend (Terminal 2)
```bash
streamlit run frontend/app.py
```
Visit **http://localhost:8501**.

---

## Deployment

This project is designed to be deployed as **two separate services** (Backend & Frontend).
See [DEPLOYMENT.md](DEPLOYMENT.md) for a step-by-step guide to hosting on Render.