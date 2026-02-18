import streamlit as st
import requests
import uuid

# ── Config ──────────────────────────────────────────────────────────
import os

# ── Config ──────────────────────────────────────────────────────────
API_URL = os.getenv("API_URL", "http://localhost:8000/chat")

st.set_page_config(
    page_title="Aria — Company Assistant",
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


# ── Header ───────────────────────────────────────────────────────────
st.title("🤖 Aria — Your Company Assistant")
st.caption("Ask me about HR policies, IT support, onboarding, and more.")
st.divider()

# ── Sidebar ──────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    st.session_state.show_context = st.toggle(
        "Show retrieved context", value=False,
        help="Display which document chunks were used to answer your question."
    )
    st.divider()

    if st.button("🗑️ Clear conversation", use_container_width=True):
        requests.delete(f"http://localhost:8000/chat/history/{st.session_state.session_id}")
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
                    API_URL,
                    json={"session_id": st.session_state.session_id, "message": prompt},
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                reply = data["reply"]
                context = data.get("context_used", "")
            except requests.exceptions.ConnectionError:
                reply = "⚠️ Cannot connect to the backend. Make sure `main.py` is running on port 8000."
                context = ""
            except Exception as e:
                reply = f"⚠️ Something went wrong: {e}"
                context = ""

        st.markdown(reply)

        if st.session_state.show_context and context:
            with st.expander("📄 Context used"):
                st.text(context)

    st.session_state.messages.append({
        "role": "assistant",
        "content": reply,
        "context": context
    })