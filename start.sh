#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/venv/bin/python"
STREAMLIT="$ROOT_DIR/venv/bin/streamlit"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8501}"

if [[ ! -x "$VENV_PYTHON" || ! -x "$STREAMLIT" ]]; then
  echo "Virtualenv not found. Run: python -m venv venv && ./venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "$ROOT_DIR/backend/.env" ]]; then
  echo "Missing backend/.env. Add GEMINI_API_KEY before starting the app."
  exit 1
fi

# Documents are indexed offline, not by the running server. Build the index once
# if it doesn't exist yet; rerun `python -m backend.index_documents` by hand
# whenever the policy PDFs or chunking logic change.
if [[ ! -f "$ROOT_DIR/backend/data/chroma_db/chroma.sqlite3" ]]; then
  echo "No vector index found. Running one-time offline indexer..."
  (cd "$ROOT_DIR" && "$VENV_PYTHON" -m backend.index_documents)
fi

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]]; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting FastAPI backend on http://127.0.0.1:$BACKEND_PORT ..."
(
  cd "$ROOT_DIR/backend"
  PORT="$BACKEND_PORT" "$VENV_PYTHON" main.py
) &
BACKEND_PID=$!

sleep 3

echo "Starting Streamlit frontend on http://127.0.0.1:$FRONTEND_PORT ..."
echo "Press Ctrl+C to stop both services."
(
  cd "$ROOT_DIR/frontend"
  "$STREAMLIT" run app.py --server.port "$FRONTEND_PORT" --server.address 127.0.0.1
)
