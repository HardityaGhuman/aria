# Project Progress & Direction

A living, high-level map of where this project is and where it is going. Kept in
sync with the detailed roadmap in `CLAUDE.md` (local-only). Update the status
markers as work lands.

**Legend:** ✅ done · 🔄 in progress · ⬜ next · ⏳ later

---

## Ultimate Goal

A **deployed, multi-user, serverless RAG chatbot** for internal company policy,
on Google Cloud (Cloud Run), with:

- **Frontend:** React (replaces the throwaway Streamlit UI).
- **Auth + RBAC:** 3-tier (employee / manager / HR) + per-user region; HR can
  reindex, employees only chat; retrieval is tier + region filtered so restricted
  docs never leak.
- **Memory split:** short-term chat in a **Redis cache**; long-term **user
  preferences + memory in Postgres**.
- **Vector store:** migrated off local Chroma-on-disk to **pgvector** (serverless
  blocker), living beside long-term memory.
- **Ingestion:** multi-source (uploads, cloud drives) via admin document
  lifecycle endpoints; eventually event-based via cron → webhook with status
  polling.
- **Observability:** standardized response envelope, per-query telemetry,
  monitoring metrics.
- **Security & resilience:** prompt/SQL-injection defense, rate limiting,
  graceful LLM error handling, provider-limit awareness.
- **Evaluation:** stays entirely offline, dev-only — never on the request path.

Each piece is designed → built → understood fully before the next.

---

## Progress Flow (high level)

```
[0] Corpus + ingestion ........... ✅ done  (7 depts, ~30 docs, 5 formats)
        │
[1] Auth + RBAC + region ......... ✅ done  (3-tier RBAC, region + temporal filter, confidential UX)
        │
[2] Security & resilience ........ ✅ done  (rate limiting, LLM retry/backoff, CORS; + prompt-injection defense, session object-auth/IDOR fix, upload caps)
        │
[3] User preferences (Postgres) .. ✅ done  (/me/preferences, injected into prompt)
        │
[4] Retrieval quality inspection . 🔄 structural fixes + consistency done; eval run + reranker pending
        │
[5] Observability ................ 🔄 response envelope done; telemetry + metrics pending
        │
[6] Admin document lifecycle ..... ✅ done  (upload/list/status/delete + reindex)
        │
[7] React frontend ............... 🔄 built  (chat+SSE, sessions, prefs, HR admin docs, dark mode)
        │
[8] pgvector + Cloud Run deploy .. ⏳  ← serverless cutover (hard blocker)
        │
[9] Background workers / events .. ⏳
        │
[10] Agentic tool loop ........... ⏳  (bounded loop + tool registry; security-first)
        │
[11] Third-party tools ........... ⏳  (Slack + Sheets + Calendar + holidays; read-before-write)
        │
[12] Drive auto-ingest ........... ⏳  (HR folder → webhook → upload)
```

**You are here:** steps 0–3 + 6 complete; 4 + 5 partially landed; **step 7 React
frontend is built** (chat with SSE streaming, session list, preferences, HR
document portal, dark mode). A **backend-standardization pass** shipped the React
prerequisites: refresh-token rotation + HttpOnly cookie, SSE streaming
(`/chat/stream`), user-owned sessions (`SessionStore` seam, Redis-ready), a uniform
error envelope, and a **frozen OpenAPI** (`docs/api/openapi.json`).

A **security-hardening pass** then closed step 2's deferred items and an audit's
findings (see *Security hardening* below). **All 91 backend tests pass.** Next
planned focus: **step 8 — pgvector + Cloud Run**.

---

## Security hardening (latest pass)

Layered prompt-injection defense + an authorization audit:

- **Prompt injection (was deferred in step 2 — now handled).** Section-0 integrity
  rules in the system prompt (no prompt-leak, fixed persona, no content-gen
  drafting); retrieved context fenced in a **per-request nonced delimiter**
  (`<policy_context_{random}>`) so a pasted closing tag can't break out; the same
  integrity preamble applied to the **meta, chitchat, and summary** routes (history
  is an injection surface too). Defenses are probabilistic — the hard boundaries
  stay the RBAC partition + JWT identity.
- **Broken object-level auth / IDOR (HIGH, fixed).** `GET`/`DELETE /chat/history`
  and `POST /chat`+`/chat/stream` lacked session-ownership checks — any user could
  read/wipe/inject into another's session. All now gated (own-or-new for sends,
  owner-only for history). Regression test: `backend/tests/test_idor.py`.
- **Upload hardening.** Bounded chunked read with a `MAX_UPLOAD_BYTES` cap (memory
  DoS), 409 on overwrite, `RATE_LIMIT_ADMIN` on upload + reindex.
- **CSRF.** Refresh Origin now exact-matched (prefix test let `…5173.evil.com`
  through); Referer needs a real path boundary.
- **Verified clean:** no secrets tracked (only `.env.example`), SQL fully
  parameterized, JWT HS256 with explicit `algorithms=[…]` (no `alg:none`), bcrypt +
  timing-equalized login, refresh rotation + jti revocation, CORS single-origin.

---

## Step Status & Detail

| # | Step | Status | Notes |
|---|------|--------|-------|
| 0 | Multi-source corpus + ingestion | ✅ | GSVH Corp corpus: 7 dept folders, ~30 docs across `.md`/`.txt`/`.pdf`/`.csv`/`.xlsx`. Frontmatter `department`/`access_tier`/`region`/`doc_type`/`version`/`status`; tabular + PDF use `.meta.yaml` sidecars. CSV/XLSX ingested rows-as-chunks. Specs: `2026-06-23`, `2026-06-24`. |
| 1 | Auth + RBAC + region | ✅ | bcrypt + JWT, `get_current_user`/`require_role`, `/auth/login`, `/auth/me` (id+role+region), HR-gated `/admin/reindex`. **3-tier RBAC** (`tiers_for_role`: employee→all, manager→all+manager, HR→all+manager+hr_only) enforced as a single app-layer **retriever partition** (security invariant unit-tested). **Region filter** (global + home region) + **superseded** exclusion as Chroma filters. Graceful **confidential message** when only restricted docs match. Specs: `2026-06-24`, `2026-06-25`. |
| 2 | Security & resilience | ✅ | SQL injection already safe (parameterized — invariant). **Done:** per-user/IP rate limiting (slowapi), LLM retry/backoff, context truncation, CORS lockdown + `/auth/refresh` CSRF (now exact-Origin). **Now also done (hardening pass):** layered prompt-injection defense (section-0 rules + per-request nonced context fence + integrity preamble on meta/chitchat/summary), **session object-auth/IDOR fix** (`test_idor.py`), upload caps (`MAX_UPLOAD_BYTES`, overwrite-409, `RATE_LIMIT_ADMIN`). **Deferred:** RPM/RPD budgeting. |
| 3 | User preferences | ✅ | `user_preferences` table; `GET/PUT /me/preferences`; tone/length/language injected into the answer prompt (best-effort — a prefs DB hiccup never breaks a chat). |
| 4 | Retrieval quality inspection | 🔄 | **Done:** overview-chunk demotion, markdown list-item chunk fix, config `RETRIEVAL_MAX_DISTANCE`, corpus number-consistency pass (PTO bands, severance). **Pending:** run offline eval harness (recall@k/MRR/context-recall + RAGAS), cross-encoder reranker, vocabulary-gap recall, router contextual-compression — all eval-gated. |
| 5 | Observability | 🔄 | **Done:** standardized response envelope (`answer`/`sources[document_id,file,section,source_type]`/`latency_ms`/`session_id`/`status`) + uniform error body. **Pending:** per-query telemetry log; monitoring metrics (P95/P99, no-answer rate, LLM-failure rate, cost/query). |
| 6 | Admin document lifecycle | ✅ | `POST /admin/documents/upload` (multipart, background ingest), `GET /admin/documents` (+status), `GET …/{id}/status`, `DELETE …/{id}` (file + chunks), `POST /admin/reindex`. Per-doc `queued→processing→indexed→failed` in `document_status`. **Deferred:** object storage (S3); ingestion stays synchronous-in-background for now. |
| 7 | React frontend | 🔄 built | React + Vite + TanStack Query app (`frontend-react/`) consuming auth (login/refresh), chat with **SSE streaming**, session list (rename/inline-delete), preferences, and the HR document portal (upload/list/status/delete/reindex). Dark mode, per-response sources, department filter. Remaining polish tracked ad hoc. |
| 8 | pgvector migration + Cloud Run deploy | ⏳ | Vectors off local Chroma into Postgres; the serverless cutover and hard hosting blocker. |
| 9 | Background workers / event ingestion | ⏳ | Cron → webhook (event-based); backend polls document-status endpoint, advances when `indexed`. |
| 10 | Agentic tool loop + tool registry | ⏳ | Bounded agent loop (`MAX_TOOL_STEPS`, default 3) inside `chat_service`; `core/tools/` registry exposes only the tools a caller's `Principal` (from JWT) may use; streams the reserved SSE `tool_call`/`tool_result`/`step` events (no contract change). **Security-first:** identity from JWT only, loop cap, gated behind `AGENT_TOOLS_ENABLED` (off = today's pure-RAG). Spec: `2026-06-26`. |
| 11 | Third-party API tools | ⏳ | *Read-before-write.* **Tier 1 (read):** leave-balance (Google Sheet mock-HRIS, principal-scoped) + policy citation; region holidays (public API); **Slack front-door** (`/aria …`, HMAC-verified). **Tier 2 (write, confirmation-gated):** book-leave (Calendar + Sheet) — `confirmation_required` before any mutation. Least-privilege Google service account. Spec: `2026-06-26`. |
| 12 | Drive auto-ingest (Tier 3) | ⏳ | HR drops a file in a watched Drive folder → webhook → existing `/admin/documents/upload`. Builds on step 9; independent of the agent loop. Spec: `2026-06-26`. |

---

## Cross-cutting requirements (land at their numbered step)

- **Security (step 2):** prompt injection ✅, rate limiting ✅, graceful LLM
  errors ✅, CORS ✅, session object-auth ✅, upload caps ✅. SQL injection already
  handled. Remaining: provider RPM/RPD budgeting.
- **Observability (step 5):** response envelope, telemetry fields, monitoring
  metrics.
- **Admin lifecycle (step 6):** the 5-endpoint surface + async per-document
  status.
- **Event ingestion (step 9):** webhook trigger + status polling.
- **Agentic tool security (steps 10–12):** identity from JWT only (never LLM args),
  confirmation gate on every write, loop cap, untrusted-context rule extended to
  tools, Slack HMAC + replay window, least-privilege Google service account,
  `AGENT_TOOLS_ENABLED` rollback switch.

Full detail lives in `CLAUDE.md → Forward Requirements`.

---

## Known architectural tensions

- **Stateless serverless vs local Chroma / in-memory BM25 / local embedding
  model** — all assume a long-lived process with stable disk. Hosting forces:
  pgvector, API embeddings or `min-instances=1`, rebuilt BM25. (Step 8.) Reminder:
  a CLI reindex requires a server restart today because Chroma/BM25 are cached in
  memory.
- **Identity is the connective tissue** — user-owned sessions are what make
  preferences, long-term memory, and RBAC possible. Session ownership
  (`owner_user_id`) now lands behind a `SessionStore` seam (Postgres impl today,
  Redis swap later with no route changes).

---

## Deferred follow-ups (noted, not blocking)

- **Router over-refusal** — the small classifier sometimes labels a legitimate
  policy question (e.g. "what laptop + cost?") `out_of_scope`. Planned fix: feed a
  compressed conversation summary to the router.
- **Confidential message is near-dormant** — broad all-tier coverage means most
  queries find an allowed match, so the confidential UX rarely fires (by chosen
  design). Optional follow-up: a relevance-gap trigger.

---

## Immediate next actions

1. **Step 7 polish** (frontend is built): tighten remaining UX, then freeze.
2. Begin **step 8 — pgvector + Cloud Run**: the serverless cutover (move vectors
   off local Chroma, rebuild BM25, decide API embeddings vs `min-instances=1`).
3. For local dev set `COOKIE_SECURE=false` so the refresh cookie is sent over
   `http://localhost` (it is `Secure` by default for prod).
4. Re-run `scripts/export_openapi.py` whenever a route or model changes so the
   frozen contract stays in sync.
