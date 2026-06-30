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

**You are here:** steps 0–3 + 6 complete; step 4 partially landed (eval rebuild done,
reranker pending); **step 5 tracing/logging done** (only metrics aggregation pending);
**step 7 React frontend is built** (chat with SSE streaming, session list, preferences,
HR document portal, dark mode); **step 10 agentic layer designed, not built** (next
focus). A **backend-standardization pass** shipped the React prerequisites: refresh-token rotation + HttpOnly cookie, SSE streaming
(`/chat/stream`), user-owned sessions (`SessionStore` seam, Redis-ready), a uniform
error envelope, and a **frozen OpenAPI** (`docs/api/openapi.json`).

### ▶ NEXT SESSION — resume here (step 10 agentic layer — DESIGNED, not built)

**Since last sync (2026-06-30, later session — two pre-agentic fixes shipped):**
1. **Retrieval recall fix (step 4):** H2-only markdown split (`CHUNK_VERSION …-h2-only-split-v11`) — `###` subsections no longer split off from their parent `##`. Fixed a real leak where a manager asking for the "interview loop stages" got "Stage 2/3 not detailed" because the four `### Stage N` chunks fragmented and TOP_K cut two. Reindexed + verified. ✅ **Gate PASSED:** `benchmark compare` post-split = hybrid recall **0.96→0.99**, precision **0.45 flat**, mrr **0.92→0.94** (`hard` mrr now 1.00) — strictly better, no precision cliff. Step 4 structural work validated; reranker / cross_doc-lift still pending.
2. **Log-noise cleanup (step 5):** `core/logging.py` now quiets LiteLLM INFO double-prints + "Provider List" banner + uvicorn OPTIONS preflight lines. Telemetry JSON untouched.
   Both verified; **full suite 131 tests green**.
3. **Conversational-quality + i18n batch (2026-07-01, TDD, 151 tests green).** Five fixes from a manager/HR convo analysis + one edge:
   - **Language honored on the policy route.** Was: preference directive sat only in the system prompt and got out-competed by the large English context block in the user turn, so policy answers stayed English while meta/chitchat honored the language (the "Hindi worked, Spanish didn't" report — really meta-vs-policy). Now: the directive is restated at the END of the augmented user turn, closest to generation (`llm._augmented_message`).
   - **Localized refusal/clarify/no-results** (es/fr/de/hi maps in `chat_service`) on the no-LLM instant paths.
   - **Filler → clarify** (`_is_low_content_message`): "umm"/"idk" short-circuit before classify/rewrite (was: rewriter fabricated a query → garbage retrieval).
   - **No copy-paste re-answer** (`_is_rephrase_request`): "explain it better"/"one by one" → re-explain directive + temp 0.4 (was: temp 0 + same query = byte-identical reply).
   - **No "the employee" framing leak**: direct-address rule in `docs/system_prompt.txt`.
   - **Edge — language-agnostic ungrounded detection**: the model emits a fixed never-translated sentinel `__NO_CONTEXT_ANSWER__` when context can't answer; the service detects THAT (not English prose) and returns localized no-results. Streaming gates the leading tokens so the raw sentinel never flashes to the user.
   - Partially addresses the deferred [[router-contextual-compression]] over-refusal item (filler + rephrase cases) — the 8B classifier still lacks full convo context for the general case.
4. **User flagged a frame of reference for the agentic build is coming** — they'll provide it; start step 10 against it.

**Step 5 observability tracing/logging is DONE** (shipped 2026-06-30: `core/trace.py`
+ `_invoke` funnel + `request_trace` rollup, 131 backend tests green, smoke-verified).
Only metrics aggregation remains pending under step 5.

**Agentic layer is fully designed; zero code written.** Brainstorm + two specs +
the model-allocation/LangGraph decision done. A fresh session starts building
inline (`writing-plans` → `executing-plans`, no subagents, /caveman full).

- Architecture spec: `docs/superpowers/specs/2026-06-26-agentic-tools-integrations-design.md` (gitignored) — agent loop, threat model, security controls, tiers.
- Pre-setup + model-allocation spec: `docs/superpowers/specs/2026-06-30-agentic-presetup-model-allocation-design.md` (gitignored) — resolves which model picks tools + LangGraph + provisioning checklist.
- **Decisions locked:** new `action` intent → no-RAG lane; **70B selects tools on every lane in v1** (hybrid already wakes 70B); validity from **native function-calling + strict JSON schemas + validate-or-repair** (unvalidated call never executes), not model size; Tier-1 reads move to 8B (`AGENT_READ_MODEL`) only after measured tool-pick accuracy; **NO LangGraph** (hand-rolled bounded loop, no `langchain-core` re-coupling); gated behind `AGENT_TOOLS_ENABLED`.
- **Build sequence:** (1) loop scaffold + `core/tools/registry.py` + `Principal` + ALL security invariants (stub tool, 70B) → (2) Tier-1 reads (leave-balance + holidays) THEN measure 8B → (3) Slack front-door → (4) Tier-2 writes (book-leave, confirmation-gated, always 70B) → (5) Tier-3 Drive auto-ingest. Each its own spec→plan→execute.
- **Next action:** invoke `superpowers:writing-plans` on **sub-step 1** (loop scaffold + registry + Principal + security invariants). Pre-setup (GCP service account, mock-HRIS sheet, Slack app) needed before Tier-1, NOT before the scaffold.
- **Test cmd:** `JWT_SECRET=dummy venv/bin/python -m pytest …` (the `eval_venv` has no pytest — use `venv`).

**Then:** step 5 metrics aggregation (P95/P99, rates, cost/query) reads the telemetry stream; hosting (step 8) is confirmed NOT a blocker to agentic (localhost-buildable).

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

**Then (done since):** logging/tracing observability (step 5) ✅ shipped. Hosting
(step 8) confirmed *not* a blocker to agentic features (localhost-buildable), so
**next focus is step 10 — the agentic layer** (designed; see the NEXT SESSION block
above). Hosting follows.

A **security-hardening pass** earlier closed step 2's deferred items and an audit's
findings (see *Security hardening* below). **All 131 backend tests pass** (after the
step-5 tracing work).

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
| 4 | Retrieval quality inspection | 🔄 | **Done:** overview-chunk demotion, markdown list-item chunk fix, config `RETRIEVAL_MAX_DISTANCE`, corpus number-consistency pass (PTO bands, severance). **H2-only markdown split (2026-06-30, `CHUNK_VERSION …-h2-only-split-v11`):** chunker now splits on `##` only — `###`+ subsections stay welded to their parent H2 (`chunking.py: _is_heading` md_mode `== 2`). Fixes an enumeration-fragmentation leak (a "## 4-Stage Interview Loop" whose four "### Stage N" subparts became separate sibling chunks → "what are the stages" retrieved some, not all; Stage 2/3 cut by TOP_K=6). Reindexed (304 chunks); verified all 4 stages now one chunk. No corpus doc uses H3-without-H2. **Eval rebuild ✅ COMPLETE** (specs/plans `2026-06-29-eval-rebuild*`): document-level harness, `doc_precision@k` headline, RAGAS isolated. All 8 tasks committed; 119 tests green. **Baseline (pre-H2-split):** hybrid wins (recall 0.96, mrr 0.92); weakest corner is `cross_doc` recall (hybrid 0.88, vector 0.62); `vocab_gap` fine, `tabular` strong. **Pending (eval-gated, measure-first):** ⚠️ **re-run `benchmark compare` to confirm the H2-only split didn't cause a precision cliff** (bigger chunks = recall↑/precision↓); then cross-encoder reranker, cross_doc recall lift, router contextual-compression. |
| 5 | Observability | 🔄 | **Done:** standardized response envelope (`answer`/`sources[document_id,file,section,source_type]`/`latency_ms`/`session_id`/`status`) + uniform error body. Per-call **LLM span** telemetry (`core/trace.py` + `_invoke` funnel: purpose, small/large model, tokens, latency, cost, status) + one **request_trace** rollup per message (query, classification, retrieval ids+scores, status, latency), correlated by `trace_id`, JSON to stdout (Cloud Logging-ready), `TELEMETRY_ENABLED` kill switch. **Log-noise cleanup (2026-06-30):** `core/logging.py: _quiet_noisy_libraries()` raises `LiteLLM`/`litellm` loggers to WARNING (kills double-printed completion/Wrapper INFO), `litellm.suppress_debug_info=True` (kills "Provider List" banner), `uvicorn.access` filter drops CORS OPTIONS preflight lines; telemetry JSON untouched. **Pending:** metrics aggregation (P95/P99, no-answer/LLM-failure rate, cost/query) + OTel span export — both read this stream. Spec/plan `2026-06-30-observability-tracing*`. |
| 6 | Admin document lifecycle | ✅ | `POST /admin/documents/upload` (multipart, background ingest), `GET /admin/documents` (+status), `GET …/{id}/status`, `DELETE …/{id}` (file + chunks), `POST /admin/reindex`. Per-doc `queued→processing→indexed→failed` in `document_status`. Upload accepts `access_tier`/`region`/`status` form fields written to a `.meta.yaml` sidecar, so HR can tier any format (csv/xlsx/pdf carry no inline frontmatter) from the portal; md/txt inline frontmatter still wins. **Deferred:** object storage (S3); ingestion stays synchronous-in-background for now. |
| 7 | React frontend | 🔄 built | React + Vite + TanStack Query app (`frontend-react/`) consuming auth (login/refresh), chat with **SSE streaming**, session list (rename/inline-delete), preferences, and the HR document portal (upload/list/status/delete/reindex). Dark mode, per-response sources, department filter. Remaining polish tracked ad hoc. |
| 7.5 | **Pre-deploy hardening** | ⏳ near-term | **DB connection pooling** — every query currently opens a fresh `psycopg.connect()` (`_connect()` in `chat_memory`/`preferences`/`users`/`tokens`/`doc_status`); under concurrency this exhausts Postgres connections + adds handshake latency. Add a pooled connection (`psycopg_pool` or pgbouncer) behind the existing `_connect()` seam — a real concurrency bug even pre-deploy, cheap to fix. Pairs with managed Postgres (Cloud SQL/Supabase) at deploy. |
| 8 | pgvector migration + Cloud Run deploy | ⏳ | Vectors off local Chroma into Postgres; the serverless cutover and hard hosting blocker. **Deploy bundle:** explicit `Dockerfile` (backend; bake the MiniLM weights), Cloud Run, managed Postgres + pgvector, secrets in Secret Manager, frontend as static build (Firebase Hosting / CDN), prod user seeding (strong per-account passwords, `COOKIE_SECURE=true`, real `FRONTEND_ORIGIN`). CI/CD via GitHub Actions (test+typecheck on PR; build+deploy on merge, OIDC/Workload Identity — no long-lived keys). |
| 9 | Background workers / event ingestion | ⏳ | Cron → webhook (event-based); backend polls document-status endpoint, advances when `indexed`. |
| 10 | Agentic tool loop + tool registry | ⏳ | Bounded agent loop (`MAX_TOOL_STEPS`, default 3) inside `chat_service`; `core/tools/` registry exposes only the tools a caller's `Principal` (from JWT) may use; streams the reserved SSE `tool_call`/`tool_result`/`step` events (no contract change). **Security-first:** identity from JWT only, loop cap, gated behind `AGENT_TOOLS_ENABLED` (off = today's pure-RAG). Spec: `2026-06-26`. **Model allocation + pre-setup decided (spec `2026-06-30-agentic-presetup-model-allocation`):** new `action` intent → no-RAG lane; **70B does tool-select on every lane in v1** (hybrid already wakes 70B); validity from **native function-calling + strict JSON schemas + validate-or-repair**, not model size; Tier-1 reads move to 8B (`AGENT_READ_MODEL`) only after measured tool-pick accuracy. **LangGraph deferred** — hand-rolled bounded loop, no `langchain-core` re-coupling; revisit only on true multi-agent / durable checkpoint / >1 interrupt. |
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

1. **Step 10 — agentic layer** (designed, next focus): invoke `writing-plans` on
   sub-step 1 (loop scaffold + `core/tools/registry.py` + `Principal` + security
   invariants, stub tool, 70B). Specs `2026-06-26` + `2026-06-30-agentic-presetup-model-allocation`.
2. **Step 5 metrics aggregation** (telemetry stream is live): avg/P95/P99 latency,
   no-answer rate, LLM-failure rate, cost/query — reads the `request_trace`/`llm_span` JSON.
3. **Step 8 — pgvector + Cloud Run** (hosting; not a blocker to agentic): move
   vectors off local Chroma, rebuild BM25, decide API embeddings vs `min-instances=1`.
4. For local dev set `COOKIE_SECURE=false` so the refresh cookie is sent over
   `http://localhost` (it is `Secure` by default for prod).
5. Re-run `scripts/export_openapi.py` whenever a route or model changes so the
   frozen contract stays in sync.
