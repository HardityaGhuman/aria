# Architecture & Request Flow

> A controlled, traceable, **read-only** multi-specialist company assistant.
> Employees ask about company policy, their own leave balance, or who's out of
> office; the system answers from authorized documents + live reads, grounded and
> cited, and every decision on the path is deterministic, bounded, and traceable.

This document is the map a new engineer (or an interviewer) reads first. It describes
the **current** architecture and the **live request flow**, names the design
trade-offs, and is honest about what is built-and-wired vs built-but-not-yet-wired.

**Status legend used throughout:**
`🟢 LIVE` — on the request path today · `🟡 BUILT, NOT WIRED` — merged + tested in isolation, the executor doesn't call it yet (lands in §6) · `⚪ PLANNED`.

---

## 1. What this is, in one breath

An **intelligent Python middleware layer**. It owns the assistant's *own* state
(users, auth, chat sessions, preferences, ingestion status) and **never** the
business's truth. The LLM does exactly three things: **decide which tool to call**,
**read policy docs (RAG)**, and **generate the reply**. Every source of truth and
every control decision (permissions, budgets, retries, failure handling) lives in
deterministic Python or in the systems a company already runs (HRIS, Calendar).

| We own (assistant state) | External systems own (business truth) |
|---|---|
| users / auth / JWT identity | leave balances → `HRISClient` (mock v1) |
| chat sessions + rolling memory | who's out of office → `CalendarClient` (mock v1) |
| preferences, doc-ingestion status | company policy = the document corpus |

**Consequence:** no business data is ever fused into the chat/RAG schema. A tool
reads an external system through an interface and forgets the value after the request.

---

## 2. System at a glance

```mermaid
flowchart TB
    subgraph Client
      FE["React SPA (Vite + TanStack Query)"]
    end

    subgraph API["FastAPI backend"]
      direction TB
      RT["routes/ (chat, auth, admin, me)"]
      SVC["services/chat_service.py<br/>orchestrator"]
      SUP["services/supervisor.py<br/>deterministic router"]
      LLM["core/llm.py<br/>LLM gateway (LiteLLM)"]
      RAG["rag/*<br/>hybrid retrieval"]
      TOOLS["core/tools/* + agents/*<br/>scoped read tools"]
      CTL["core/control/* + read_planner<br/>🟡 control layer"]
      OBS["core/trace.py<br/>telemetry"]
    end

    subgraph Stores
      PG[("PostgreSQL<br/>sessions, users, prefs, tokens")]
      CH[("ChromaDB<br/>vectors, on disk")]
      BM[("BM25 index<br/>in-memory cache")]
    end

    subgraph External["External truth (behind interfaces)"]
      HRIS["HRISClient → MockHRIS"]
      CAL["CalendarClient → MockCalendar"]
      PROV["LLM provider (Groq / gpt-oss)"]
    end

    FE -->|"HTTPS + JWT"| RT --> SVC
    SVC --> LLM & RAG & SUP & OBS
    SUP --> TOOLS
    TOOLS --> HRIS & CAL
    LLM --> PROV
    RAG --> CH & BM
    SVC --> PG
    CTL -.->|"wires in at §6"| SVC
```

**Tech stack:** FastAPI · React · LiteLLM (model-agnostic gateway) · ChromaDB (cosine)
· BM25 (`rank-bm25`) · sentence-transformers (`all-MiniLM-L6-v2`, local, 384-dim) ·
PostgreSQL (`psycopg3`) · pypdf + LangChain splitter for ingestion.

**Models (swappable via env):**
- **Large — `MODEL_NAME` = `groq/openai/gpt-oss-120b`** — the grounded answer + summarization.
- **Small — `ROUTER_MODEL_NAME` = `groq/openai/gpt-oss-20b`** — classification, query rewriting, and tool selection (`AGENT_READ_MODEL` defaults to the router).

> **Why two models?** Routing/extraction must be fast and cheap and run on every
> message; the expensive 120B is reserved for the one call that needs reasoning — the
> final grounded answer. Validity comes from **native function-calling + strict JSON
> schemas + validate-or-repair**, not from model size.

---

## 3. The live request lifecycle 🟢

`POST /chat` (sync) and `POST /chat/stream` (SSE) run the **same** orchestration; only
final delivery differs. Entry point: `services/chat_service.py`
(`generate_chat_reply` / `stream_chat_reply`).

```mermaid
flowchart TD
    A["POST /chat · JWT verified → Principal"] --> B["_prepare_history_async<br/>(offloaded to thread)"]
    B --> C{"cheap guards"}
    C -->|"bare filler 'umm'"| Cx["clarify, stop"]
    C -->|"else"| D["classify_query · 20B router"]

    D -->|out_of_scope| E1["hardcoded refusal · no LLM answer"]
    D -->|meta| E2["get_meta_response · from history"]
    D -->|chitchat| E3["get_chitchat_response · warm brief"]
    D -->|policy / hr / calendar| F["_answer_policy_query"]

    F --> G["supervisor.route(label, principal)<br/>→ ONE specialist (RBAC fallback → policy-agent)"]
    G --> H["supervisor.run_specialist<br/>gather scoped tool (if any)"]
    H --> I["query rewrite → hybrid retrieval → tier/region/status filter"]
    I --> J["get_llm_response · 120B<br/>context + tool_note fused, grounded"]
    J --> K["grounding guardrail<br/>(refusal / __NO_CONTEXT__ sentinel)"]
    K --> L["append_exchange → Postgres<br/>(atomic owner claim)"]
    L --> M["ChatResponse envelope"]

    E1 & E2 & E3 --> L
```

### Classification — the six lanes (`core/llm.py: classify_query`, 20B)

| Label | Meaning | Path |
|---|---|---|
| `policy` | wants info from policy docs | Policy-agent → pure RAG |
| `hr` | wants **their own** live HR data ("leaves left?") | HR-agent → `leave_balance` + policy citation |
| `calendar` | wants the live **team** OOO list ("who's out?") | Calendar-agent → `whos_out` |
| `meta` | about the conversation ("what did you just say?") | answered from history, no RAG |
| `chitchat` | greeting / about Aria | brief reply, no RAG |
| `out_of_scope` | unrelated / "write me an email" | hardcoded refusal, no answer-model call |

An unknown/malformed classifier response normalizes to `policy` (fail-toward-grounded).

### Retrieval — hybrid + RRF (`rag/`)

```mermaid
flowchart LR
    Q["search query<br/>(history-aware rewrite)"] --> V["vector search<br/>MiniLM → Chroma (cosine)"]
    Q --> B["BM25 search<br/>term freq / IDF"]
    V --> R["Reciprocal Rank Fusion<br/>1/(k+rank_v) + 1/(k+rank_b)"]
    B --> R
    R --> P["RBAC tier partition<br/>(app-layer, rag/retriever.py)"]
    P --> F["region + status filters<br/>(rag/strategies.py)"]
    F --> C["top-K=6 context block"]
```

- **Hybrid** beats either alone: vector covers semantic paraphrase, BM25 covers exact
  terms; RRF rewards chunks strong in both.
- **Security-critical ordering:** the **tier partition happens in app code before**
  restricted chunk text is ever formatted for the LLM — a blocked chunk's text never
  reaches the model. Region (`global` + caller's home) and `status` (`superseded`
  excluded) are Chroma/BM25 `where` filters. All threaded from the JWT: route → service
  → retriever.

---

## 4. Agents & orchestration 🟢

**Hierarchical, hand-rolled, bounded.** One deterministic **supervisor** delegates to
**exactly one specialist**, gets a structured result, and continues. No parallel
fan-out, no agent-to-agent delegation, no shared mutable state, no LangGraph. Each
specialist = the Unit-1 agent loop parameterized with a **scoped tool registry** + an
**RBAC floor**.

```mermaid
flowchart TD
    CLS["classification label"] --> SUP["supervisor.route()"]
    SUP -->|hr| HR["hr-agent<br/>registry = {leave_balance}"]
    SUP -->|calendar| CAL["calendar-agent<br/>registry = {whos_out}"]
    SUP -->|policy / fallback| POL["policy-agent<br/>registry = {} (pure RAG)"]

    HR --> HRIS["HRISClient → MockHRIS"]
    CAL --> CALS["CalendarClient → MockCalendar"]

    SUP -. "RBAC: role can't reach chosen<br/>→ fall back to policy-agent" .-> POL
```

**The isolation guarantee** (`core/agents/build.py` — the single composition root):
each specialist's registry holds **only its own tool**, never a superset. Concretely
— a calendar request can *never* reach the HRIS read, and an HR request can *never*
reach the calendar read. This is what the plan validator and registry RBAC both
assume; the composition root is where it's enforced, and a regression test asserts
`hr-agent.registry.get("whos_out") is None`.

**Supervisor contract** (`services/supervisor.py`):
- `route(label, principal)` — label → one specialist; **RBAC fallback** to policy-agent
  if the caller's role can't reach the chosen specialist (never surface a specialist
  above the caller's role).
- `run_specialist(...)` — runs the scoped registry through the gather loop; **best-effort**:
  flag off / no tools / no visible specs / any gather failure ⇒ a no-note result, so
  the request **degrades to pure RAG** rather than failing.
- `build_tool_note(...)` — folds tool outputs into a **trusted, template-built** note
  (typed server fields only, never raw external text) that the answer model may state
  as fact.

**The kill switch:** `AGENT_TOOLS_ENABLED` (default **false**). Off ⇒ the tool path is
inert and behavior is **byte-identical to pure RAG** — the regression guard for the
whole agentic layer.

---

## 5. The bounded agent loop 🟢

`services/agent_loop.py: run_agent_loop(...)`. Read tools run in `gather_only` mode —
select → validate → (one repair) → invoke → return the typed result to the supervisor.

```mermaid
flowchart TD
    S["select_tool_call · 20B<br/>native function-calling"] --> T{"tool chosen?"}
    T -->|no| DONE["return gathered results"]
    T -->|unknown tool| ERR1["tool_error"]
    T -->|yes| V{"validate_args<br/>vs strict JSON schema"}
    V -->|valid| INV["registry.invoke<br/>(RBAC re-check + reserved-arg strip)"]
    V -->|invalid & budget left| RE["ONE repair re-ask"]
    V -->|invalid & no budget| ERR2["tool_error (fail, don't overspend)"]
    RE --> V2{"valid now?"}
    V2 -->|yes| INV
    V2 -->|no| ERR2
    INV --> DONE
```

Bounds that make it safe regardless of what the model emits:
- **`MAX_TOOL_STEPS` = 3** — hard loop cap on cost/latency.
- **Validate-or-repair** — a schema-invalid call gets exactly **one** repair re-ask,
  then hard-errors. An unvalidated call **never executes**.
- **Selector-call budget** — repairs count against the budget, so a repair storm can't
  bypass the cap.

---

## 6. The control layer 🟡 (`core/control/*` + `services/read_planner.py`)

Built and tested in isolation; **the executor doesn't call it yet** (wires in at §6 of
the rescope). This is the deterministic spine that will replace the ad-hoc
`classify → route → gather → answer` with a typed, validated, traceable pipeline.

```mermaid
flowchart LR
    CTX["RequestContext<br/>JWT Principal + intent<br/>+ tier/region snapshots"] --> BP["build_plan(intent)<br/>FIXED table, no LLM"]
    BP --> VP["validate_plan()<br/>budgets · registry · RBAC<br/>· pinned to canonical table"]
    VP -->|invalid| STOP["TerminalState.INVALID_PLAN"]
    VP -->|valid| EXE["execute (≤1 tool, ≤1 retrieval)"]
    EXE --> FAIL["policies.py<br/>failure → ONE ValidationAction"]
    EXE --> OUT["RequestOutcome<br/>typed terminal state + SourceRefs"]
    BP -.-> TR["BoundaryTracer<br/>redacted §14 events"]
    VP -.-> TR
    EXE -.-> TR
    OUT -.-> TR
```

### 6.1 The fixed plan table (`read_planner.build_plan`) — canonical §7

Plans are built in **pure Python**, never by an LLM. If the model could widen a budget
or pick a specialist, a prompt injection could escalate a read into a broader action.

| Intent | Specialist | Tools | Retrieval | Budget |
|---|---|---|---|---|
| `policy` | `policy-agent` | — | required | 1 retrieval |
| `hr` | `hr-agent` | `leave_balance` | required | 1 tool + 1 retrieval |
| `calendar` | `calendar-agent` | `whos_out` | optional | 1 tool |
| `meta` / `chitchat` | — | — | none | 1 answer call |
| `out_of_scope` | — | — | none | no answer call |

`validate_plan` rejects a plan that: mismatches the plan version; exceeds the global
caps (≤1 tool, ≤1 retrieval); is internally inconsistent; names an unknown/unreachable
specialist or a tool outside its registry; **or diverges from the canonical table for
its intent** (`plan_off_table`) — so `build_plan` is the *only* source of a valid plan.

### 6.2 Deterministic failure policies (`core/control/policies.py`) — canonical §13

Every failure maps to **exactly one** `ValidationAction` — no LLM chooses recovery.
Retry-once is a property of the code (via a passed attempt count), not the caller.

```mermaid
flowchart TD
    QR["query rewrite fails"] --> C1["CONTINUE (use original)"]
    BM["BM25 unavailable"] --> C2["CONTINUE (vector-only, degraded)"]
    TA["invalid tool args"] --> R1["RETRY once → then STOP"]
    UT["unknown/unauthorized tool"] --> S1["STOP · invalid_plan"]
    H1["HRIS transient"] --> R2["RETRY once → PARTIAL (policy-only) or tool_unavailable"]
    H2["HRIS no record"] --> C3["CONTINUE (valid no-record)"]
    CT["calendar transient"] --> R3["RETRY once → tool_unavailable"]
    CR["invalid calendar range"] --> S2["STOP · reject pre-exec"]
    NC["no authorized context"] --> BL["BLOCK (restricted) / no_results"]
    GR["ungrounded answer"] --> S3["STOP · grounding_failed"]
```

### 6.3 Terminal states (`TerminalState`) & the client contract

Every request ends in exactly one of ten internal states; the outcome maps to a small
client-facing envelope status so the UI never learns the internal failure taxonomy:

```mermaid
stateDiagram-v2
    [*] --> ok
    [*] --> partial:        HRIS down, policy-only
    [*] --> no_results:     authorized but empty
    [*] --> blocked:        only restricted matched (RBAC)
    [*] --> refused:        out of scope
    [*] --> tool_unavailable: backend down after retry
    [*] --> invalid_plan:   → opaque "error"
    [*] --> grounding_failed: → opaque "error"
    [*] --> timeout:        → opaque "error"
    [*] --> internal_error: → opaque "error"
```

Client envelope statuses: `ok · partial · no_results · blocked · refused ·
tool_unavailable` (the last four internal states collapse to a single opaque `error`).

### 6.4 Boundary tracing (`core/control/tracing.py`) — canonical §14

`BoundaryTracer` emits one **redacted** structured event per transition
(`request_received → … → request_completed / request_failed`). Redaction is the whole
point: every payload comes from an allowlisted `to_trace_record()` or a safe scalar, so
**no raw message, answer text, document body, live payload, email, or unscrubbed id**
ever reaches a trace. `answer_completed` carries latency + source count, never the
answer. Reuses the existing telemetry sink + `TELEMETRY_ENABLED` kill switch.

---

## 7. Harness engineering — resilience, retries, budgets

The "make it not fall over" layer. Each control maps to a concrete threat.

```mermaid
flowchart LR
    subgraph Edge
      RL["rate limit (slowapi)<br/>per JWT-sub / IP"]
      CORS["CORS locked to FRONTEND_ORIGIN<br/>+ /auth CSRF Origin check"]
    end
    subgraph LLM["every LLM call · core/llm.py _invoke"]
      RETRY["call_with_retry<br/>exp backoff"]
      TRUNC["truncate_to_token_budget"]
      TO["LLM_TIMEOUT_SECONDS"]
    end
    subgraph Loop
      CAP["MAX_TOOL_STEPS"]
      VOR["validate-or-repair"]
    end
    subgraph Async
      OFF["blocking work → asyncio.to_thread<br/>(history, retrieval, persist)"]
      WAIT["asyncio.wait_for timeouts"]
    end
```

| Concern | Mechanism | Where | Default |
|---|---|---|---|
| Transient provider errors | retry + exponential backoff | `call_with_retry` | `LLM_MAX_RETRIES=2`, base `0.5s` |
| Context overflow | token-budget truncation before the call | `truncate_to_token_budget` | `LLM_CONTEXT_TOKEN_BUDGET=6000` |
| Slow provider | per-call timeout | `_invoke` / `wait_for` | `LLM_TIMEOUT_SECONDS=45` |
| Runaway tool loop | hard step cap | `run_agent_loop` | `MAX_TOOL_STEPS=3` |
| Bad tool args | one repair, then fail | `agent_loop` + `validate_args` | 1 repair |
| Long sessions | summarize + prune older turns | `chat_service` / `chat_memory` | `MAX_HISTORY_TOKENS=2000`, keep last 4 |
| Event-loop starvation | offload blocking calls to threads | `_run_blocking`, `_prepare_history_async` | — |
| Abuse / budget | per-user/IP edge rate limits | `core/ratelimit.py` | `30/min` chat |
| Tool failure | best-effort gather → degrade to pure RAG | `run_specialist` | — |

> **Degradation philosophy:** the request should get *quieter*, not *break*. A failed
> rewrite → original query. A failed BM25 → vector-only. A failed tool gather → pure
> RAG. A down HRIS → policy-only partial. Only an unrecoverable/authorization failure
> stops the request.

---

## 8. Security invariants (never weaken these)

- **Identity from the JWT only.** Tools receive a server-built `Principal`; any
  `user_id`/`email` in LLM-generated args is stripped and ignored. Bob's question can
  never read Alice's balance.
- **Tier partition before formatting.** Restricted chunk text is filtered in app code
  before it's ever put in a prompt. Postgres has no RLS — this app-layer gate is the boundary.
- **Tool RBAC twice.** Checked when *exposing* schemas (`specs_for`) and when *invoking*
  (`registry.invoke`), by `min_role`.
- **No write tools registered anywhere.** A definition-of-done invariant for the read-only rescope.
- **Prompt injection is defense-in-depth.** System-prompt integrity rules + a per-request
  **nonced** context fence + integrity preamble on every LLM route. Document/chat text
  can *request* but never *authorize* a tool call.
- **SQL always parameterized.** `psycopg` `%s`, never string interpolation.
- **Object-level authz (IDOR closed).** Every session route is ownership-checked; atomic
  owner-claim on first write.
- **Trace redaction.** Telemetry logs ids + scores + codes, never document bodies,
  emails, JWTs, or raw external payloads; ids pseudonymizable in prod.

---

## 9. Data & storage ownership

```mermaid
flowchart TB
    subgraph Ours["Assistant state — PostgreSQL"]
      S1["chat_sessions (owner_user_id, summary)"]
      S2["chat_messages"]
      S3["users · refresh_tokens (jti)"]
      S4["user_preferences · document_status"]
    end
    subgraph Retrieval
      C1["ChromaDB — vectors + metadata (on disk)"]
      C2["BM25 — in-memory, rebuilt on reindex"]
    end
    subgraph Theirs["Business truth — behind interfaces, NEVER persisted here"]
      E1["MockHRIS (leave balances)"]
      E2["MockCalendar (OOO)"]
    end
```

**Known tension (the serverless blocker):** on-disk Chroma, in-memory BM25, and the
~90MB local embedding model all assume a long-lived process with a stable disk — the
opposite of stateless, scale-to-zero Cloud Run. Hosting forces the move to **pgvector**
(vectors beside memory in Postgres), rebuilt BM25, and either API embeddings or
`min-instances=1`. That migration is the prerequisite for deploy.

---

## 10. Transports & the response envelope

Both transports produce the same shape; SSE streams it as events.

```
POST /chat         → { answer, sources[], latency_ms, session_id, status }
POST /chat/stream  → SSE: token* → sources → done (same envelope) | error
```

Errors everywhere use `{ "error": { code, message, detail } }`. `sources` carry
`document_id · file · section · source_type` (never a filesystem path). Status ∈
`ok | partial | no_results | blocked | refused | tool_unavailable`.

---

## 11. Configuration knobs (env, with defaults)

| Var | Default | Purpose |
|---|---|---|
| `MODEL_NAME` | `groq/openai/gpt-oss-120b` | large answer model |
| `ROUTER_MODEL_NAME` | `groq/openai/gpt-oss-20b` | classify / rewrite / tool-select |
| `AGENT_TOOLS_ENABLED` | `false` | master switch for the tool path (off = pure RAG) |
| `AGENT_READ_MODEL` | = router | model that selects/extracts tool calls |
| `MAX_TOOL_STEPS` | `3` | agent-loop cap |
| `RETRIEVAL_STRATEGY` / `RETRIEVAL_TOP_K` | `hybrid` / `6` | retrieval |
| `LLM_MAX_RETRIES` / `LLM_RETRY_BASE_DELAY` | `2` / `0.5s` | transient-error backoff |
| `LLM_TIMEOUT_SECONDS` / `LLM_CONTEXT_TOKEN_BUDGET` | `45` / `6000` | per-call bounds |
| `MAX_HISTORY_TOKENS` | `2000` | summarize-and-prune threshold |
| `JWT_SECRET` | *(required)* | server refuses to boot without it |
| `FRONTEND_ORIGIN` | `http://localhost:5173` | CORS + CSRF origin |
| `TELEMETRY_ENABLED` | `true` | boundary/span trace kill switch |

---

## 12. Where the code lives

```
backend/
  routes/        thin HTTP layer (chat · auth · admin · me)
  services/
    chat_service.py   orchestrator (history → classify → route → gather → answer → persist)
    supervisor.py     deterministic route() + run_specialist() + build_tool_note()
    read_planner.py   🟡 build_plan() fixed table + validate_plan()
    agent_loop.py     bounded run_agent_loop (validate-or-repair, gather_only)
  core/
    llm.py            LLM gateway: classify / answer / stream / select_tool_call; retry + truncate
    control/          🟡 models · policies · tracing  (typed plans, failure map, boundary traces)
    tools/            Principal · ToolRegistry (RBAC) · leave_balance · whos_out
    agents/           Specialist value objects + build_specialists() (scoped registries)
    hris/  calendar/  external-truth interfaces + seeded mocks
    trace.py          request-scoped trace_id + LLM spans + request rollup
    auth · tokens · chat_memory · preferences · ratelimit · errors
  rag/                loaders · chunking · embedding · vector_store · bm25 · strategies · retriever
```

---

## 13. Honest status (as of 2026-07-07, branch `control-layer`)

- 🟢 **Live:** auth + RBAC · hybrid RAG with tier/region/status filtering · supervisor +
  three isolated specialists · bounded agent loop · retries/timeouts/budgets ·
  observability spans · React frontend. Tool path gated behind `AGENT_TOOLS_ENABLED`.
- 🟡 **Built, not wired:** the §4 control layer (typed plans, `validate_plan`,
  deterministic failure policies, boundary tracer). It is fully unit-tested but the
  executor doesn't call it yet — **§6 is the integration** that makes it the live path
  and collapses sync/stream into one shared pipeline.
- ⚪ **Planned (must-ship order):** pgvector cutover + Cloud Run deploy · durable
  ingestion jobs · evidence viewer · committed evaluation metrics.

**Deliberately out of scope (need a new written decision):** any write tool
(leave requests, calendar events), confirmation gates, Slack/Jira/GitHub, parallel
fan-out, LLM-chosen plans/budgets, LLM judges, long-term semantic memory.

> **One-line self-summary:** a bounded read-only multi-specialist assistant whose
> routing, permissions, evidence, failures, recovery actions, and (soon) full request
> trace are explicit, testable, and deterministic — the LLM decides *what to read* and
> *how to phrase*, and nothing else.
