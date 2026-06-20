import os
import time
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
      [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
      .stTabs [data-baseweb="tab-list"] { gap: 8px; }
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
if "eval_rows" not in st.session_state:
    st.session_state.eval_rows = []  # one row per grounded answer


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


def call_evaluate(message: str, reply: str, context: str) -> dict:
    try:
        resp = requests.post(
            f"{CHAT_ENDPOINT}/evaluate",
            json={"message": message, "reply": reply, "context_used": context},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"evaluated": False}


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
            for document in documents:
                name = document["filename"]
                try:
                    st.download_button(
                        name,
                        data=fetch_document_bytes(name),
                        file_name=name,
                        mime="application/pdf",
                        use_container_width=True,
                    )
                except Exception:
                    st.caption(name)
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
        st.session_state.eval_rows = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()


# ── Header ───────────────────────────────────────────────────────────
st.title("Aria — Policy Assistant")
st.caption("Grounded answers from your company policy documents.")

# chat_input is declared at the top level so it stays pinned to the bottom of
# the page; the new turn itself is rendered inside the Chat tab below.
prompt = st.chat_input("Ask about a company policy…")

# ── Tabs ─────────────────────────────────────────────────────────────
tab_chat, tab_eval = st.tabs(["Chat", "Evaluation"])

with tab_chat:
    if not st.session_state.messages and not prompt:
        st.info("Ask a question to get started — e.g. *How many days of PTO do I get?*")

    # Replay prior conversation.
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            srcs = msg.get("sources") or []
            if msg["role"] == "assistant" and srcs:
                st.caption("Sources: " + ", ".join(sorted({s["source"] for s in srcs})))

    # Handle the new turn at the bottom, so the spinner appears below the
    # conversation rather than at the top of the page.
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Aria is thinking…"):
                started = time.time()
                reply, context, sources = call_chat(prompt)
                latency = time.time() - started
            st.markdown(reply)
            if sources:
                st.caption("Sources: " + ", ".join(sorted({s["source"] for s in sources})))

            # Score only grounded policy answers (those with retrieved context).
            if context:
                with st.spinner("Scoring response…"):
                    result = call_evaluate(prompt, reply, context)
                st.session_state.eval_rows.append(
                    {
                        "#": len(st.session_state.eval_rows) + 1,
                        "question": prompt,
                        "faithfulness": result.get("faithfulness"),
                        "answer_relevance": result.get("answer_relevance"),
                        "context_relevance": result.get("context_relevance"),
                        "latency": round(latency, 2),
                        "rationale": (result.get("rationale") or "")[:90],
                    }
                )

        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.messages.append(
            {"role": "assistant", "content": reply, "sources": sources}
        )


with tab_eval:
    rows = st.session_state.eval_rows
    st.caption(
        "Each grounded answer is scored 1–5 by an LLM judge on faithfulness "
        "(is it supported by the documents?), answer relevance, and context "
        "relevance (retrieval quality)."
    )

    if not rows:
        st.info("Ask a policy question to see live evaluation metrics here.")
    else:
        def _avg(key):
            vals = [r[key] for r in rows if isinstance(r[key], int)]
            return sum(vals) / len(vals) if vals else None

        def _fmt(value):
            return f"{value:.1f} / 5" if value is not None else "—"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Faithfulness", _fmt(_avg("faithfulness")))
        c2.metric("Answer relevance", _fmt(_avg("answer_relevance")))
        c3.metric("Context relevance", _fmt(_avg("context_relevance")))
        lat = [r["latency"] for r in rows]
        c4.metric("Avg latency", f"{sum(lat) / len(lat):.2f}s")

        def _cell(value):
            return value if value is not None else "—"

        table = [
            {
                "#": r["#"],
                "Question": (r["question"][:60] + "…") if len(r["question"]) > 60 else r["question"],
                "Faithful": _cell(r["faithfulness"]),
                "Answer Rel.": _cell(r["answer_relevance"]),
                "Context Rel.": _cell(r["context_relevance"]),
                "Latency (s)": r["latency"],
                "Notes": r["rationale"],
            }
            for r in rows
        ]
        st.dataframe(table, use_container_width=True, hide_index=True)
