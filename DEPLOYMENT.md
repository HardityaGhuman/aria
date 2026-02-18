# Deployment Guide (Render)

This project requires **two separate services** on Render because Streamlit and FastAPI run on different ports/protocols.

## Prerequisites
-   A GitHub repository with this code pushed.
-   A Render.com account.
-   Access to your `GEMINI_API_KEY`.

---

## Service 1: The Backend (FastAPI)

1.  **New Web Service** → Connect your repo.
2.  **Name:** `company-chatbot-backend`
3.  **Runtime:** Python 3
4.  **Build Command:** `pip install -r requirements.txt`
5.  **Start Command:** `uvicorn backend.main:app --host 0.0.0.0 --port 10000`
6.  **Environment Variables:**
    -   `GEMINI_API_KEY`: (Your Google API Key)
    -   `MODEL_NAME`: `gemini-2.5-flash`
    -   `CHROMA_DB_PATH`: `/opt/render/project/src/backend/data/chroma_db` (or just leave default if not persisting/using Disk)
    -   `DOCS_PATH`: `/opt/render/project/src/backend/data/docs`
    -   `SYSTEM_PROMPT_PATH`: `/opt/render/project/src/docs/system_prompt.txt`
    -   **Important:** Since ChromaDB writes to disk, if you want the index to survive restarts, you need to add a **Render Disk** attached to `/opt/render/project/src/backend/data`. Otherwise, it will re-index every time (which is fine for small docs).

7.  **Deploy.**
    -   Once live, copy the URL (e.g., `https://company-chatbot-backend.onrender.com`).

---

## Service 2: The Frontend (Streamlit)

1.  **New Web Service** → Connect your repo.
2.  **Name:** `company-chatbot-frontend`
3.  **Runtime:** Python 3
4.  **Build Command:** `pip install -r requirements.txt`
5.  **Start Command:** `streamlit run frontend/app.py --server.port 10000 --server.address 0.0.0.0`
6.  **Environment Variables:**
    -   `API_URL`: **Paste your Backend URL here** and add `/chat` at the end.
        -   Example: `https://company-chatbot-backend.onrender.com/chat`

7.  **Deploy.**

---

## Persistent Storage Note
ChromaDB is a file-based database. On Render (free tier), the filesystem is ephemeral.
-   **If you restart the backend, the index disappears** until it re-indexes on startup.
-   Since we have `startup_event` in `main.py` that re-indexes files from `backend/data/docs`, this **will work fine** automatically as long as your `.txt` files are in the git repo!
-   **However**, if you want to *upload* files dynamically later without committing them, you MUST pay for a **Render Disk**.
