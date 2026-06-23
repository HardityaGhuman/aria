import os
import uuid
from pathlib import Path

import streamlit as st
import requests
from dotenv import load_dotenv

# Load backend/.env so values set there (DOC_DRIVE_URL, API_BASE_URL) are visible
# to the frontend too — Streamlit doesn't read it on its own.
load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

# ── Config ──────────────────────────────────────────────────────────
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_ENDPOINT = f"{API_BASE_URL}/chat"
# When the policy PDFs live in Google Drive, set DOC_DRIVE_URL to the public
# share link; the sidebar then shows a single link to it (shown regardless of
# backend/index state). Left unset, we serve indexed PDFs from the backend.
DOC_DRIVE_URL = os.getenv("DOC_DRIVE_URL", "").strip()

st.set_page_config(page_title="Aria · Policy Assistant", layout="centered")

# ── Styling: minimal monochrome ──────────────────────────────────────
st.markdown(
    """
    <style>
      #MainMenu, footer, [data-testid="stStatusWidget"] { visibility: hidden; }
      [data-testid="stDecoration"] { display: none; }
      .block-container { padding-top: 2.4rem; padding-bottom: 6rem; max-width: 840px; }
      [data-testid="stChatMessage"] {
        background: #141414;
        border: 1px solid #262626;
        border-radius: 14px;
        padding: 0.35rem 0.95rem;
      }
      .stButton > button,
      .stDownloadButton > button,
      [data-testid="stLinkButton"] a {
        border-radius: 10px;
        border: 1px solid #2e2e2e;
        font-weight: 500;
      }
      h1 { letter-spacing: -0.02em; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session State ────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "messages" not in st.session_state:
    st.session_state.messages = []  # {"role", "content", "sources"}


# ── Backend helpers ──────────────────────────────────────────────────
@st.cache_data(ttl=30)
def fetch_policy_documents():
    resp = requests.get(f"{CHAT_ENDPOINT}/documents", timeout=5)
    resp.raise_for_status()
    return resp.json().get("documents", [])


@st.cache_data(ttl=300)
def fetch_document_bytes(filename: str) -> bytes:
    resp = requests.get(f"{CHAT_ENDPOINT}/documents/{filename}/download", timeout=15)
    resp.raise_for_status()
    return resp.content


def render_retrieval(sources, context):
    """Show exactly what was retrieved: per-chunk source path, department,
    access tier, section, and the exact context block sent to the model."""
    if not sources:
        return
    # One-line summary of the distinct documents behind the answer.
    st.caption("Sources: " + ", ".join(sorted({s["source"] for s in sources})))
    with st.expander(f"Retrieved {len(sources)} passage(s) — inspect"):
        for s in sources:
            dept = s.get("department") or "—"
            tier = s.get("access_tier") or "—"
            section = s.get("section")
            distance = s.get("distance")
            dist_str = f"  ·  dist `{distance}`" if distance is not None else ""
            st.markdown(
                f"**{s.get('source')}**  ·  dept `{dept}`  ·  tier `{tier}`"
                f"  ·  chunk `{s.get('chunk')}`{dist_str}"
            )
            if section:
                st.caption(f"§ {section}")
        if context:
            st.markdown("---")
            st.caption("Exact context sent to the model:")
            st.code(context, language="markdown")


def call_chat(message: str):
    """Return (reply, context, sources). Errors become a friendly reply."""
    try:
        resp = requests.post(
            CHAT_ENDPOINT,
            json={"session_id": st.session_state.session_id, "message": message},
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["reply"], data.get("context_used", ""), data.get("sources", [])
    except requests.exceptions.ConnectionError:
        return "Cannot reach the assistant. Is the backend running?", "", []
    except requests.exceptions.HTTPError as e:
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        return f"Backend error: {detail}", "", []
    except Exception as e:
        return f"Something went wrong: {e}", "", []


# ── Sidebar: minimal ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Policy Library")

    if DOC_DRIVE_URL:
        # Hosted documents: one link to the shared drive, independent of the
        # backend so it always shows even before the index is built.
        st.link_button("Open policy documents", DOC_DRIVE_URL, use_container_width=True)
    else:
        # Local mode: list indexed PDFs with download buttons from the backend.
        try:
            documents = fetch_policy_documents()
        except Exception:
            documents = []

        if documents:
            _mime = {"pdf": "application/pdf", "md": "text/markdown", "txt": "text/plain"}
            for document in documents:
                rel_path = document["filename"]                  # e.g. hr/employment-basics.md
                label = os.path.basename(rel_path)
                dept = document.get("department")
                try:
                    st.download_button(
                        f"{label}" + (f"  ·  {dept}" if dept else ""),
                        data=fetch_document_bytes(rel_path),
                        file_name=label,
                        mime=_mime.get(document.get("type", ""), "application/octet-stream"),
                        use_container_width=True,
                    )
                except Exception:
                    st.caption(rel_path)
        else:
            st.caption("No policy documents are indexed yet.")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        try:
            requests.delete(
                f"{CHAT_ENDPOINT}/history/{st.session_state.session_id}", timeout=5
            )
        except Exception:
            pass
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()


# ── Header ───────────────────────────────────────────────────────────
st.title("Aria — Policy Assistant")
st.caption("Grounded answers from your company policy documents.")

# chat_input is declared at the top level so it stays pinned to the bottom of
# the page; the new turn itself is rendered inside the Chat tab below.
prompt = st.chat_input("Ask about a company policy…")

# ── Chat ─────────────────────────────────────────────────────────────
if not st.session_state.messages and not prompt:
    st.info("Ask a question to get started — e.g. *How many days of PTO do I get?*")

# Replay prior conversation.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            render_retrieval(msg.get("sources") or [], msg.get("context", ""))

# Handle the new turn at the bottom, so the spinner appears below the
# conversation rather than at the top of the page.
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Aria is thinking…"):
            reply, context, sources = call_chat(prompt)
        st.markdown(reply)
        render_retrieval(sources, context)

    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "sources": sources, "context": context}
    )
