# Design: Auth + RBAC + Preferences + Retrieval Check (local, one week)

**Date:** 2026-06-22
**Status:** Approved design — not yet implemented
**Scope:** Local only. No cloud, no React, no S3. The first three sub-projects of the
larger hosting roadmap (see CLAUDE.md → "Project Direction & Roadmap").

---

## Goal

Introduce **identity** to the chatbot. Today a `session_id` is an anonymous,
client-supplied string. After this work, every session is *owned* by an
authenticated user, gated by role (HR vs employee), with per-user preferences —
the hook that future long-term memory and per-user features hang on.

This is deliberately backend-first, matching the goal of becoming a backend/AI
engineer. The Streamlit wiring is minimal and throwaway; it will be replaced by
React later.

---

## Realistic one-week plan (~5 working days)

| Day | Sub-project | Deliverables |
|---|---|---|
| **1–2** | JWT auth + RBAC | `users` table; bcrypt hashing; `POST /auth/login` (HS256 JWT); `get_current_user` + `require_role("hr")` dependencies; chat behind auth, reindex behind HR; minimal Streamlit login form. |
| **3** | User preferences | `user_preferences` table; `GET/PUT /me/preferences`; sessions tied to `user_id`; preferences injected into the answer prompt. |
| **4** | Retrieval quality check | Run the existing eval harness (`benchmark compare` + RAGAS subset); interpret recall@k / MRR / context-recall; pick ONE evidence-backed win; commit a short results note. No retrieval rebuild. |
| **5** | Integration + buffer | End-to-end test login → chat → prefs → HR reindex; harden error paths; buffer for auth spillover. |

**Realism flag:** auth done properly (hashing, token verify, role dependencies,
securing every endpoint, Streamlit wiring) is a genuine 2 days for someone
learning each line. Refresh tokens, signup, password reset, and email
verification are cut to make the week fit.

---

## Sub-project 1 — JWT auth + RBAC (Day 1–2)

### `users` table (`core/users.py`, mirroring `core/chat_memory.py`)

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL PK | stable user identity |
| `email` | TEXT UNIQUE NOT NULL | login handle |
| `password_hash` | TEXT NOT NULL | bcrypt via `passlib` |
| `role` | TEXT NOT NULL CHECK (role IN ('hr','employee')) | the RBAC axis |
| `created_at` | TIMESTAMPTZ DEFAULT now() | |

### User provisioning — **seeded, no signup**

`seed_users.py` inserts 1 HR + 1–2 employee accounts. Passwords come from env
vars (or an interactive prompt), **never hardcoded in git**. No registration
endpoint exists — this mirrors how internal tools provision accounts and removes
the self-assigned-HR security hole.

### `core/auth.py` — three small responsibilities

1. `hash_password` / `verify_password` (bcrypt).
2. `create_access_token(user)` → HS256 JWT signed with `JWT_SECRET`, carrying
   `sub=user_id`, `role`, `exp` (default 8h). `decode_token(...)` verifies
   signature + expiry.
3. FastAPI dependencies:
   - `get_current_user` — reads `Authorization: Bearer …`, decodes → user or **401**.
   - `require_role("hr")` — depends on the first → **403** on mismatch.

   *401 = "I don't know who you are"; 403 = "I know who you are and you're not
   allowed."*

### `routes/auth.py`

- `POST /auth/login` — verify creds → `{access_token, token_type, role}`.
  **Generic error on failure** (no user enumeration).
- `GET /auth/me` — return the current user.

### Securing existing + new endpoints

- `POST /chat`, history routes → `get_current_user` (any authenticated user).
- **`POST /admin/reindex`** (`routes/admin.py`) → `require_role("hr")`; calls the
  existing `initialize_vectorstore()` **synchronously**; returns
  `{indexed, skipped, deleted}`. (Upgrade to a background job when we hit
  serverless.)

### Identity ↔ sessions

Add `owner_user_id` to `chat_sessions`; stamp it on creation; reject cross-user
access on `/chat` and `/history`. **This is the most important structural change**
— it's what makes preferences and future long-term memory possible.

### Streamlit (throwaway)

Login form → store token + role in `st.session_state` → attach `Authorization`
header to every backend call → show "Update policies" only when `role == "hr"`.

### Config additions

`JWT_SECRET` (loud warning if unset locally), `JWT_EXPIRY_HOURS`.

---

## Sub-project 2 — User preferences (Day 3)

### `user_preferences` table (`core/preferences.py`)

| Column | Type | Notes |
|---|---|---|
| `user_id` | BIGINT PK → users(id) ON DELETE CASCADE | one row per user |
| `response_length` | TEXT CHECK ('concise','detailed') DEFAULT 'concise' | |
| `tone` | TEXT CHECK ('formal','casual') DEFAULT 'formal' | |
| `updated_at` | TIMESTAMPTZ | |

### Routes

- `GET /me/preferences`, `PUT /me/preferences` — operate only on the current user.

### Injection

The chat flow now needs `user_id` (from `get_current_user`), so
`generate_chat_reply(session_id, message)` becomes `(session_id, message, user)`.
The service fetches prefs, builds a one-line directive (e.g. *"Answer concisely.
Use a formal tone."*), and appends it to the system prompt threaded through
`get_llm_response`. Defaults apply if no row exists.

**Preferences chosen this week:** response length (concise/detailed) + tone
(formal/casual). Both are pure prompt-injection — they shape the answer, not
retrieval, so no re-indexing or chunk metadata is needed.

---

## Sub-project 3 — Retrieval quality check (Day 4)

Measure-and-decide, **not build** — the harness already exists
(`backend/eval/`).

- Run `benchmark compare` (vector vs bm25 vs hybrid) + a small RAGAS subset.
- Interpret: is hybrid actually winning? On hard questions, is the failure a
  *retrieval miss* (low recall@k) or a *generation* problem (good context, weak
  answer)?
- Pick **exactly one** evidence-backed action: tune `RETRIEVAL_TOP_K`, adjust the
  `MAX_DISTANCE` floor, or formally log *"add a cross-encoder reranker"* as
  next-week's follow-up.
- Deliverable: a short committed results note (numbers + the one decision).
  **No retrieval rebuild this week.**

---

## Testing

- **Auth:** hash/verify round-trip; token create→decode for valid / expired /
  tampered; `require_role` → 403; login → 401.
- **Ownership:** user A cannot read user B's history.
- **Prefs:** PUT→GET round-trip; directive present in the assembled prompt.
- **Reindex:** non-HR → 403; HR → stats.
- **Retrieval:** the eval harness *is* the test.

---

## Call-chain changes (the ripples)

- Routes thread `current_user` (id, role) into the service.
- `generate_chat_reply(session_id, message)` → `(session_id, message, user)`.
- `chat_sessions` gains `owner_user_id`; ownership enforced on read/write.

---

## Explicitly out of scope this week

React, S3, Cloud Run, multi-source connectors, n8n, refresh tokens, signup,
password reset, email verification. Deliberate cuts to make the week fit.

---

## Risks

- **Auth spillover** — it always takes longer than expected; Day 5 is the buffer.
- **Streamlit + auth is clunky** (`session_state` resets on rerun) — acceptable
  because it's throwaway.
- **Synchronous reindex blocks the request** — fine for a few local PDFs; a known
  upgrade for the serverless step.
