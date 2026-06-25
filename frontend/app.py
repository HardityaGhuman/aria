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
if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.role = None
    st.session_state.email = None


# ── Auth helpers ─────────────────────────────────────────────────────
def auth_headers() -> dict:
    token = st.session_state.token
    return {"Authorization": f"Bearer {token}"} if token else {}


def logout(message: str | None = None):
    """Clear the session and bounce back to the login screen."""
    st.session_state.token = None
    st.session_state.role = None
    st.session_state.email = None
    st.session_state.messages = []
    if message:
        st.session_state.auth_notice = message
    st.rerun()


def render_login():
    """Login screen. Shown until a token is stored. Stops the rest of the app."""
    st.title("Aria — Policy Assistant")
    st.caption("Sign in to ask about company policies.")
    if notice := st.session_state.pop("auth_notice", None):
        st.warning(notice)
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)
    if submitted:
        try:
            resp = requests.post(
                f"{API_BASE_URL}/auth/login",
                json={"email": email, "password": password},
                timeout=15,
            )
        except requests.exceptions.ConnectionError:
            st.error("Cannot reach the server. Is the backend running?")
            st.stop()
        if resp.status_code == 200:
            data = resp.json()
            st.session_state.token = data["access_token"]
            st.session_state.role = data["role"]
            st.session_state.email = email
            st.rerun()
        elif resp.status_code == 401:
            st.error("Invalid email or password.")
        elif resp.status_code == 503:
            st.error("The server is unavailable right now. Try again shortly.")
        else:
            st.error("Login failed. Try again.")
    st.stop()


# ── Backend helpers ──────────────────────────────────────────────────
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
    """Return (reply, context, sources). Errors become a friendly reply.

    A 401 means the token expired or is invalid → log the user out so they can
    sign in again rather than seeing a raw error.
    """
    try:
        resp = requests.post(
            CHAT_ENDPOINT,
            json={"session_id": st.session_state.session_id, "message": message},
            headers=auth_headers(),
            timeout=90,
        )
    except requests.exceptions.ConnectionError:
        return "Cannot reach the assistant. Is the backend running?", "", []
    except Exception as e:
        return f"Something went wrong: {e}", "", []

    if resp.status_code == 401:
        logout("Your session expired. Please sign in again.")
    if resp.status_code == 503:
        return "The server is temporarily unavailable. Try again shortly.", "", []
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.reason)
        except Exception:
            detail = resp.reason
        return f"Backend error: {detail}", "", []

    data = resp.json()
    return data["reply"], data.get("context_used", ""), data.get("sources", [])


# ── Auth gate ────────────────────────────────────────────────────────
# Everything below requires a logged-in user. render_login() calls st.stop()
# when there is no token, so the chat UI never renders for an anonymous user.
if not st.session_state.token:
    render_login()


# ── Sidebar: minimal ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### Policy Library")
    st.caption(f"Signed in as **{st.session_state.email}** · `{st.session_state.role}`")

    # HR-only: trigger a reindex of the policy corpus.
    if st.session_state.role == "hr":
        st.divider()
        if st.button("Update policies (reindex)", use_container_width=True):
            with st.spinner("Reindexing…"):
                try:
                    resp = requests.post(
                        f"{API_BASE_URL}/admin/reindex", headers=auth_headers(), timeout=300
                    )
                except requests.exceptions.ConnectionError:
                    st.error("Cannot reach the server.")
                    resp = None
            if resp is not None:
                if resp.status_code == 401:
                    logout("Your session expired. Please sign in again.")
                elif resp.status_code == 403:
                    st.error("You don't have access to that.")
                elif resp.ok:
                    s = resp.json()
                    st.success(
                        f"Reindexed: {s.get('indexed', 0)} added, "
                        f"{s.get('skipped', 0)} skipped, {s.get('deleted', 0)} removed."
                    )
                else:
                    st.error("Reindex failed. Check server logs.")

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        try:
            requests.delete(
                f"{CHAT_ENDPOINT}/history/{st.session_state.session_id}",
                headers=auth_headers(),
                timeout=5,
            )
        except Exception:
            pass
        st.session_state.messages = []
        st.session_state.session_id = str(uuid.uuid4())
        st.rerun()

    if st.button("Log out", use_container_width=True):
        logout()


# ── Header ───────────────────────────────────────────────────────────
st.title("Aria — Policy Assistant")
st.caption("Grounded answers from your company policy documents.")

# chat_input is declared at the top level so it stays pinned to the bottom of
# the page; the new turn itself isq rendered inside the Chat tab below.
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
