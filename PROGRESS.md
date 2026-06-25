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
[2] Security & resilience ........ ⬜ NEXT
        │
[3] User preferences (Postgres) .. ⏳
        │
[4] Retrieval quality inspection . 🔄 structural fixes + consistency done; eval run + reranker pending
        │
[5] Observability ................ ⏳
        │
[6] Admin document lifecycle ..... ⏳
        │
[7] React frontend ............... ⏳
        │
[8] pgvector + Cloud Run deploy .. ⏳  ← serverless cutover (hard blocker)
        │
[9] Background workers / events .. ⏳
```

**You are here:** steps 0 + 1 complete and verified end-to-end (3-tier RBAC,
region isolation, superseded-doc exclusion, confidential-on-block UX). Corpus
expanded to the GSVH multi-format set. Some of step 4's structural/consistency
work landed alongside. Next planned focus: **step 2 — security & resilience**.

---

## Step Status & Detail

| # | Step | Status | Notes |
|---|------|--------|-------|
| 0 | Multi-source corpus + ingestion | ✅ | GSVH Corp corpus: 7 dept folders, ~30 docs across `.md`/`.txt`/`.pdf`/`.csv`/`.xlsx`. Frontmatter `department`/`access_tier`/`region`/`doc_type`/`version`/`status`; tabular + PDF use `.meta.yaml` sidecars. CSV/XLSX ingested rows-as-chunks. Specs: `2026-06-23`, `2026-06-24`. |
| 1 | Auth + RBAC + region | ✅ | bcrypt + JWT, `get_current_user`/`require_role`, `/auth/login`, `/auth/me` (id+role+region), HR-gated `/admin/reindex`. **3-tier RBAC** (`tiers_for_role`: employee→all, manager→all+manager, HR→all+manager+hr_only) enforced as a single app-layer **retriever partition** (security invariant unit-tested). **Region filter** (global + home region) + **superseded** exclusion as Chroma filters. Graceful **confidential message** when only restricted docs match. Specs: `2026-06-24`, `2026-06-25`. |
| 2 | Security & resilience | ⬜ | SQL injection already safe (parameterized — invariant). TODO: prompt-injection mitigation, API rate limiting, LLM retries/backoff + context-length truncation, RPM/RPD/token budgeting, CORS tighten before deploy. |
| 3 | User preferences | ⏳ | `user_preferences` table; `GET/PUT /me/preferences`; injected into the answer prompt. |
| 4 | Retrieval quality inspection | 🔄 | **Done:** overview-chunk demotion, markdown list-item chunk fix, config `RETRIEVAL_MAX_DISTANCE`, corpus number-consistency pass (PTO bands, severance). **Pending:** run offline eval harness (recall@k/MRR/context-recall + RAGAS), cross-encoder reranker, vocabulary-gap recall, router contextual-compression — all eval-gated. |
| 5 | Observability | ⏳ | Standardized response envelope (`answer`/`sources[document_id,file,page,source_type]`/`latency_ms`); per-query telemetry log; monitoring metrics (P95/P99, no-answer rate, LLM-failure rate, cost/query). |
| 6 | Admin document lifecycle | ⏳ | `POST /admin/documents/upload`, `GET …/{id}/status`, `DELETE …/{id}`, `POST /admin/reindex` (exists); per-doc `queued→processing→indexed→failed`; object storage (S3); index rebuild semantics. |
| 7 | React frontend | ⏳ | Replaces Streamlit; consumes auth + document-admin + chat endpoints. |
| 8 | pgvector migration + Cloud Run deploy | ⏳ | Vectors off local Chroma into Postgres; the serverless cutover and hard hosting blocker. |
| 9 | Background workers / event ingestion | ⏳ | Cron → webhook (event-based); backend polls document-status endpoint, advances when `indexed`. |

---

## Cross-cutting requirements (land at their numbered step)

- **Security (step 2):** prompt injection, rate limiting, graceful LLM errors,
  provider limits, CORS. SQL injection already handled.
- **Observability (step 5):** response envelope, telemetry fields, monitoring
  metrics.
- **Admin lifecycle (step 6):** the 5-endpoint surface + async per-document
  status.
- **Event ingestion (step 9):** webhook trigger + status polling.

Full detail lives in `CLAUDE.md → Forward Requirements`.

---

## Known architectural tensions

- **Stateless serverless vs local Chroma / in-memory BM25 / local embedding
  model** — all assume a long-lived process with stable disk. Hosting forces:
  pgvector, API embeddings or `min-instances=1`, rebuilt BM25. (Step 8.) Reminder:
  a CLI reindex requires a server restart today because Chroma/BM25 are cached in
  memory.
- **Identity is the connective tissue** — user-owned sessions are what make
  preferences, long-term memory, and RBAC possible. (Session ownership
  `owner_user_id` deferred to the Redis cutover.)

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

1. Live-re-verify in the UI after a reindex + server restart (PTO=24, region
   isolation, confidential-on-block).
2. Begin **step 2 — security & resilience** (rate limiting + graceful LLM error
   handling first; they are partly needed already).
