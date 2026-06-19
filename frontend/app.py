import os

import streamlit as st
import requests
import uuid

# ── Config ──────────────────────────────────────────────────────────
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
CHAT_ENDPOINT = f"{API_BASE_URL}/chat"

st.set_page_config(
    page_title="Company Policy Assistant",
    page_icon="🤖",
    layout="centered"
)

# ── Session State Init ───────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "messages" not in st.session_state:
    st.session_state.messages = []  # {"role": "user"/"assistant", "content": str}

if "show_context" not in st.session_state:
    st.session_state.show_context = False


@st.cache_data(ttl=30)
def fetch_policy_documents():
    docs_response = requests.get(f"{CHAT_ENDPOINT}/documents", timeout=5)
    docs_response.raise_for_status()
    return docs_response.json().get("documents", [])


# ── Header ───────────────────────────────────────────────────────────
st.title("Company Policy Assistant")
st.caption("Ask me about company policies, documents, and more.")
st.divider()

# ── Sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    try:
        documents = fetch_policy_documents()
    except Exception:
        documents = []

    if documents:
        st.markdown("**Indexed documents**")
        for document in documents:
            size_kb = document["size_bytes"] / 1024
            st.caption(f"{document['filename']} ({size_kb:.1f} KB)")
    else:
        st.caption(
            "No documents indexed. An administrator adds PDFs to the docs "
            "folder and runs the offline indexer."
        )

    st.divider()

    st.session_state.show_context = st.toggle(
        "Show retrieved context", value=False,
        help="Display which document chunks were used to answer your question."
    )
    st.divider()

    if st.button("🗑️ Clear conversation", use_container_width=True):
        requests.delete(f"{CHAT_ENDPOINT}/history/{st.session_state.session_id}")
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    st.divider()
    st.markdown("**Session ID**")
    st.code(st.session_state.session_id[:8] + "...", language=None)
    st.caption("Each session maintains its own conversation history.")


# ── Chat History ─────────────────────────────────────────────────────
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and st.session_state.show_context and msg.get("context"):
            with st.expander("📄 Context used"):
                st.text(msg["context"])


# ── Chat Input ────────────────────────────────────────────────────────
if prompt := st.chat_input("Ask Aria something..."):

    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call backend
    with st.chat_message("assistant"):
        with st.spinner("Aria is thinking..."):
            try:
                response = requests.post(
                    CHAT_ENDPOINT,
                    json={"session_id": st.session_state.session_id, "message": prompt},
                    timeout=75
                )
                response.raise_for_status()
                data = response.json()
                reply = data["reply"]
                context = data.get("context_used", "")
                sources = data.get("sources", [])
            except requests.exceptions.ConnectionError:
                reply = "⚠️ Cannot connect to the backend. Make sure `main.py` is running on port 8000."
                context = ""
                sources = []
            except requests.exceptions.HTTPError as e:
                try:
                    detail = e.response.json().get("detail", str(e))
                except Exception:
                    detail = str(e)
                reply = f"⚠️ Backend error: {detail}"
                context = ""
                sources = []
            except Exception as e:
                reply = f"⚠️ Something went wrong: {e}"
                context = ""
                sources = []

        st.markdown(reply)

        if sources:
            st.caption("Sources: " + ", ".join(sorted({s["source"] for s in sources})))

        if st.session_state.show_context and context:
            with st.expander("📄 Context used"):
                st.text(context)

    st.session_state.messages.append({
        "role": "assistant",
        "content": reply,
        "context": context,
        "sources": sources,
    })
