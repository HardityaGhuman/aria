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
[5] Observability ................ 🔄 envelope + LLM tracing/rollup done; metrics dashboard pending
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

### ▶ NEXT SESSION — resume here (step 5 observability, tracing/logging — PLANNED, not built)

**Planning complete; zero code written.** Brainstorm + spec + full TDD plan done
this session. A fresh session executes the plan inline (`executing-plans`, no
subagents).

- Spec: `docs/superpowers/specs/2026-06-30-observability-tracing-design.md` (gitignored)
- Plan: `docs/superpowers/plans/2026-06-30-observability-tracing.md` (gitignored) — top has an "⏸ EXECUTION STATUS" block; **start at Task 1**.
- **What it builds:** deterministic LLM trace-back. `core/trace.py` (contextvar `trace_id`, propagates across `asyncio.to_thread`) + a single `_invoke()` funnel in `core/llm.py` wrapping all 7 `litellm.completion` calls → one JSON **span** each (purpose, **small-vs-large model**, exact tokens, latency, cost, status/error). `chat_service` emits one **request_trace** rollup/message (query, classification, retrieval ids+scores, status, latency). Join on `trace_id`. Sink = stdout JSON via a `telemetry` logger (Cloud Logging-ready). `TELEMETRY_ENABLED` kill switch. No new deps.
- **Decisions locked (brainstorm):** sink = JSON logs (NOT LangSmith — app calls litellm not LangChain runnables, so LangSmith auto-traces nothing + ships policy text out); redaction = query + chunk ids + scores, **never doc body** (tested invariant); scope = spans + rollup only (OTel, metrics dashboard, DB table all deferred).
- **5 tasks:** (1) trace.py + config flag + tests, (2) `_invoke` funnel + 5 non-stream calls + test_telemetry, (3) rewrite + streaming spans, (4) chat_service trace+rollup both paths + test, (5) regression(119+~13)+smoke+PROGRESS update. TDD, all code in the plan.
- **Test cmd:** `JWT_SECRET=dummy venv/bin/python -m pytest …` (the `eval_venv` has no pytest — use `venv`).

**Then:** metrics aggregation (P95/P99, rates, cost/query) reads this stream; then hosting (step 8). Order eval → observability → deploy.

---

### Eval rebuild (step 4) — ✅ COMPLETE (all 8 tasks)

Eval rebuild **done** (inline `executing-plans`, branch `new-frontend`). All 8
tasks committed; **119 backend tests green**.

- Spec: `docs/superpowers/specs/2026-06-29-eval-rebuild-design.md`
- Plan: `docs/superpowers/plans/2026-06-29-eval-rebuild.md`
- **Done:** Task 1 doc-level metrics, Task 2 dataset loader + corpus-id validation, Task 3 hand-authored **43-question** dataset, Task 4 document-level benchmark (difficulty + query_type breakdown), Task 5 answers.py RAGAS-export on the new schema, Task 6 run_eval.py metric-key update, Task 7 README + cleanup (RAGAS runner needed no change), Task 8 baseline run.

**Baseline (k=6, full corpus, no RBAC filter — `benchmark compare`):**

| strategy | recall | precision | hit | mrr |
|---|---|---|---|---|
| vector | 0.96 | 0.46 | 1.00 | 0.90 |
| bm25 | 0.91 | 0.37 | 0.93 | 0.82 |
| **hybrid** | **0.96** | 0.45 | 0.98 | **0.92** |

- **Hybrid wins** (best mrr, strong recall). bm25 weakest.
- **Weakest corner = `cross_doc` recall** (needs 2+ docs): vector 0.62 / bm25 0.75 / hybrid 0.88. Hybrid rescues but still lowest bucket — **the leak to chase in tuning.**
- **`vocab_gap` is fine** (vector 1.00, hybrid 0.91) — paraphrase recall not the feared problem; RRF lets bm25's keyword-miss drag hybrid ~0.09.
- **`tabular` strong** (recall 1.00, precision 0.67 — highest-precision corner).
- Precision ~0.45 everywhere is expected (k=6, mostly single expected doc → 5/6 chunks "noise" by construction); not actionable alone.

**Next (eval-gated, measure-first):** cross-encoder reranker, cross_doc recall lift, router contextual-compression. Baseline report under `backend/eval/results/` (gitignored).

**Decisions locked:** document-level scoring (match on chunk `metadata["source"]`,
exact equality), hand-authored ~24-question dataset against the real 30-doc corpus,
two layers kept (fast local retrieval metrics + RAGAS in the isolated venv), raw
ranking over the full corpus (no RBAC filter — isolates ranking quality). New
headline metric `doc_precision@k` for the planned document viewer. A **fairness
bar** (non-trivial questions: vocab-gap with zero keyword overlap, cross-doc,
tabular, distractor-prone) is baked into the spec.

**To execute next time:** invoke `superpowers:executing-plans` on the plan,
inline (no subagents). Tasks 1–2, 4–7 are mechanical TDD; Task 3 = author the
dataset (I draft from the corpus, you review); Task 8 = run the baseline (needs
`python -m backend.index_documents` first). Out of scope until *after* the
baseline: reranker, chunking, retrieval tuning — measure first.

**Then (later sessions):** logging/tracing observability (step 5), then hosting
(step 8 — confirmed *not* a blocker to agentic features, which are localhost-buildable).

A **security-hardening pass** then closed step 2's deferred items and an audit's
findings (see *Security hardening* below). **All 98 backend tests pass.** Next
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
  DoS), 409 on overwrite, `RATE_LIMIT_ADMIN` on upload + reindex. Portal-set
  `access_tier`/`region`/`status` are validated against the same allow-lists the
  retrieval filters use before they reach Chroma metadata (a bad tier can't
  mis-gate access).
- **CSRF.** Refresh Origin now exact-matched (prefix test let `…5173.evil.com`
  through); Referer needs a real path boundary.
- **Rate-limit bypass (MEDIUM, fixed).** The limiter keyed on the raw Bearer
  token, and `/auth/refresh` mints a fresh token each call — so refresh-then-spam
  handed out a new bucket per rotation, defeating the chat limit. Now keyed on the
  stable `sub` claim (bucket follows the user), and `/auth/refresh` + `/auth/logout`
  are themselves rate-limited. Regression: `backend/tests/test_ratelimit.py`.
- **Verified clean:** no secrets tracked (only `.env.example`), SQL fully
  parameterized, JWT HS256 with explicit `algorithms=[…]` (no `alg:none`), bcrypt +
  timing-equalized login, refresh rotation + jti revocation, CORS single-origin,
  500s logged server-side and never leaked to clients.
- **Open, deferred to step 8 (serverless-coupled):** the slowapi limiter is
  in-process — needs a shared store (Redis/Memorystore) under multi-instance Cloud
  Run; stateless access tokens mean up to a full TTL (30 min) of stale access for a
  revoked/role-changed user (shorten TTL or add a deny-list at deploy).

---

## Step Status & Detail

| # | Step | Status | Notes |
|---|------|--------|-------|
| 0 | Multi-source corpus + ingestion | ✅ | GSVH Corp corpus: 7 dept folders, ~30 docs across `.md`/`.txt`/`.pdf`/`.csv`/`.xlsx`. Frontmatter `department`/`access_tier`/`region`/`doc_type`/`version`/`status`; tabular + PDF use `.meta.yaml` sidecars. CSV/XLSX ingested rows-as-chunks. Specs: `2026-06-23`, `2026-06-24`. |
| 1 | Auth + RBAC + region | ✅ | bcrypt + JWT, `get_current_user`/`require_role`, `/auth/login`, `/auth/me` (id+role+region), HR-gated `/admin/reindex`. **3-tier RBAC** (`tiers_for_role`: employee→all, manager→all+manager, HR→all+manager+hr_only) enforced as a single app-layer **retriever partition** (security invariant unit-tested). **Region filter** (global + home region) + **superseded** exclusion as Chroma filters. Graceful **confidential message** when only restricted docs match. Specs: `2026-06-24`, `2026-06-25`. |
| 2 | Security & resilience | ✅ | SQL injection already safe (parameterized — invariant). **Done:** per-user/IP rate limiting (slowapi), LLM retry/backoff, context truncation, CORS lockdown + `/auth/refresh` CSRF (now exact-Origin). **Now also done (hardening pass):** layered prompt-injection defense (section-0 rules + per-request nonced context fence + integrity preamble on meta/chitchat/summary), **session object-auth/IDOR fix** (`test_idor.py`), upload caps (`MAX_UPLOAD_BYTES`, overwrite-409, `RATE_LIMIT_ADMIN`), **rate-limit key fix** (per-`sub` not per-token; `/auth/refresh`+`/auth/logout` limited — closes the refresh-then-spam bypass; `test_ratelimit.py`). **Deferred (step 8):** shared-store limiter for multi-instance, short-TTL/deny-list for instant revocation; RPM/RPD budgeting. |
| 3 | User preferences | ✅ | `user_preferences` table; `GET/PUT /me/preferences`; tone/length/language **folded into the system prompt** (not a trailing history turn — that was under-weighted, so language/length appeared ignored), best-effort (a prefs DB hiccup never breaks a chat). Regional English variants normalized to the default so a length-only change emits no redundant language directive. |
| 4 | Retrieval quality inspection | 🔄 | **Done:** overview-chunk demotion, markdown list-item chunk fix, config `RETRIEVAL_MAX_DISTANCE`, corpus number-consistency pass (PTO bands, severance). **Eval rebuild ✅ COMPLETE** (specs/plans `2026-06-29-eval-rebuild*`): document-level harness, `doc_precision@k` headline, RAGAS isolated. All 8 tasks committed (metrics, dataset loader+validation, 43-Q dataset, benchmark, answers/run_eval schema fix, README, **baseline run**); 119 tests green. **Baseline:** hybrid wins (recall 0.96, mrr 0.92); weakest corner is `cross_doc` recall (hybrid 0.88, vector 0.62); `vocab_gap` fine, `tabular` strong. **Pending (eval-gated, measure-first):** cross-encoder reranker, cross_doc recall lift, router contextual-compression. |
| 5 | Observability | 🔄 | **Done:** standardized response envelope (`answer`/`sources[document_id,file,section,source_type]`/`latency_ms`/`session_id`/`status`) + uniform error body. Per-call **LLM span** telemetry (`core/trace.py` + `_invoke` funnel: purpose, small/large model, tokens, latency, cost, status) + one **request_trace** rollup per message (query, classification, retrieval ids+scores, status, latency), correlated by `trace_id`, JSON to stdout (Cloud Logging-ready), `TELEMETRY_ENABLED` kill switch. **Pending:** metrics aggregation (P95/P99, no-answer/LLM-failure rate, cost/query) + OTel span export — both read this stream. Spec/plan `2026-06-30-observability-tracing*`. |
| 6 | Admin document lifecycle | ✅ | `POST /admin/documents/upload` (multipart, background ingest), `GET /admin/documents` (+status), `GET …/{id}/status`, `DELETE …/{id}` (file + chunks), `POST /admin/reindex`. Per-doc `queued→processing→indexed→failed` in `document_status`. Upload accepts `access_tier`/`region`/`status` form fields written to a `.meta.yaml` sidecar, so HR can tier any format (csv/xlsx/pdf carry no inline frontmatter) from the portal; md/txt inline frontmatter still wins. **Deferred:** object storage (S3); ingestion stays synchronous-in-background for now. |
| 7 | React frontend | 🔄 built | React + Vite + TanStack Query app (`frontend-react/`) consuming auth (login/refresh), chat with **SSE streaming**, session list (rename/inline-delete), preferences, and the HR document portal (upload/list/status/delete/reindex). Dark mode, per-response sources, department filter. Remaining polish tracked ad hoc. |
| 7.5 | **Pre-deploy hardening** | ⏳ near-term | **DB connection pooling** — every query currently opens a fresh `psycopg.connect()` (`_connect()` in `chat_memory`/`preferences`/`users`/`tokens`/`doc_status`); under concurrency this exhausts Postgres connections + adds handshake latency. Add a pooled connection (`psycopg_pool` or pgbouncer) behind the existing `_connect()` seam — a real concurrency bug even pre-deploy, cheap to fix. Pairs with managed Postgres (Cloud SQL/Supabase) at deploy. |
| 8 | pgvector migration + Cloud Run deploy | ⏳ | Vectors off local Chroma into Postgres; the serverless cutover and hard hosting blocker. **Deploy bundle:** explicit `Dockerfile` (backend; bake the MiniLM weights), Cloud Run, managed Postgres + pgvector, secrets in Secret Manager, frontend as static build (Firebase Hosting / CDN), prod user seeding (strong per-account passwords, `COOKIE_SECURE=true`, real `FRONTEND_ORIGIN`). CI/CD via GitHub Actions (test+typecheck on PR; build+deploy on merge, OIDC/Workload Identity — no long-lived keys). |
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
