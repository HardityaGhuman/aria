"""core/control/models.py
------------------------
§4.1 / canonical §6 — the control-layer vocabulary.

These are pure typed value objects, frozen so nothing mutates them mid-request.
They are the contract every later control file (`policies`, `validation`,
`tracing`, the read planner) speaks in:

  - `TerminalState`      — the finite set of ways a request can end.
  - `RetrievalRequirement` — whether a plan's specialist must/may/mustn't retrieve.
  - `RequestContext`     — the server-owned inputs to planning (identity + intent).
  - `ReadPlan`           — the typed, versioned execution plan (built in Python).
  - `StepResult`         — one execution step's record (retrieval or tool).
  - `SourceRef`          — one trace-safe cited-source reference (ids only).
  - `ValidationOutcome`  — a validator's verdict + the deterministic action to take.
  - `RequestOutcome`     — the single typed terminal outcome of the whole request.

Redaction invariant: the *trace* view of these objects (via `to_trace_record()`)
carries no document body, no raw HRIS/calendar payload, no user message, no answer
text, and no chain-of-thought — only ids, scores, codes, and small typed fields.
The objects themselves still hold the sensitive request state (`message`, `answer`)
because the executor needs it; the allowlisted serializers are the boundary. Never
`dataclasses.asdict()` a `RequestContext` or `RequestOutcome` into a trace — that
would leak the raw message/answer. Use `to_trace_record()`.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Mapping

from backend.core.tools.principal import Principal

# The plan schema version. Bump only on a breaking change to ReadPlan's shape so a
# stored/traced plan is always interpretable against the code that produced it.
PLAN_VERSION = "read-v1"

# A trace-safe scalar — the only value types allowed into StepResult.meta so a
# recorded step can never smuggle a document body or a live-data payload.
Scalar = str | int | float | bool | None


class TerminalState(str, enum.Enum):
    """Every request terminates in exactly one of these — independent of model
    behavior. `str` mixin so the value serializes as its name for traces/JSON."""
    OK = "ok"
    PARTIAL = "partial"                 # a real but incomplete answer (e.g. policy-only when HRIS is down)
    NO_RESULTS = "no_results"           # authorized but nothing matched
    BLOCKED = "blocked"                 # only restricted evidence matched (RBAC)
    REFUSED = "refused"                 # out-of-scope / content-gen request
    INVALID_PLAN = "invalid_plan"       # plan failed validation before execution
    TOOL_UNAVAILABLE = "tool_unavailable"
    GROUNDING_FAILED = "grounding_failed"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


class RetrievalRequirement(enum.Enum):
    """Whether the plan's specialist retrieves policy evidence."""
    REQUIRED = "required"   # policy / hr — an answer without evidence is a failure
    OPTIONAL = "optional"   # calendar — evidence may fuse in but isn't mandatory
    NONE = "none"           # meta / chitchat / out_of_scope — no retrieval at all


# The deterministic recovery actions a validator can order (canonical §6.4). No LLM
# chooses these — the failure policies (§4.3) map a failure to exactly one.
class ValidationAction(str, enum.Enum):
    CONTINUE = "continue"   # step/plan/answer is fine — proceed
    RETRY = "retry"         # transient failure — one bounded retry
    PARTIAL = "partial"     # degrade to a real-but-incomplete answer
    BLOCK = "block"         # authorized but restricted — RBAC block
    STOP = "stop"           # unrecoverable — terminate the request


# Terminal → client-facing envelope `status`. `partial` and `tool_unavailable`
# survive verbatim (the client renders them distinctly); every other control/error
# terminal collapses to a single opaque "error" so the client never learns our
# internal failure taxonomy (invalid_plan / grounding_failed / timeout / internal).
_ENVELOPE_STATUSES = {
    TerminalState.OK: "ok",
    TerminalState.PARTIAL: "partial",
    TerminalState.NO_RESULTS: "no_results",
    TerminalState.BLOCKED: "blocked",
    TerminalState.REFUSED: "refused",
    TerminalState.TOOL_UNAVAILABLE: "tool_unavailable",
}


@dataclass(frozen=True)
class RequestContext:
    """The server-owned inputs to planning. Identity is the JWT-derived Principal
    only — never anything the model produced.

    `message` is sensitive request state, NOT trace-safe: it is excluded from
    `to_trace_record()`. `allowed_tiers`/`allowed_regions` are immutable snapshots
    of the caller's authorization taken once at the authenticated request boundary
    so planning and validation read one stable view (they cannot drift mid-request)."""
    trace_id: str
    principal: Principal
    session_id: str
    message: str
    intent: str  # the classifier label: policy | hr | calendar | meta | chitchat | out_of_scope
    allowed_tiers: tuple[str, ...] = ()
    allowed_regions: tuple[str, ...] = ()

    def to_trace_record(self) -> dict:
        """Allowlisted, redacted view. Omits `message` (sensitive) and the
        principal's email (PII); keeps only trace-safe identity + intent + the
        authorization snapshots."""
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "intent": self.intent,
            "user_id": self.principal.user_id,
            "role": self.principal.role,
            "region": self.principal.region,
            "allowed_tiers": self.allowed_tiers,
            "allowed_regions": self.allowed_regions,
        }


@dataclass(frozen=True)
class ReadPlan:
    """The typed, versioned execution plan (canonical §6.2). Built deterministically
    from the intent (§4.2), never by an LLM. Budgets + `timeout_ms` are hard caps the
    executor enforces. `needs_live_data` marks a plan whose answer may assert live
    external truth (so grounding must see a validated tool result). `assumptions`
    records the deterministic choices the planner baked in (e.g. caller region) so a
    trace explains why the plan looked the way it did. Every field is trace-safe."""
    intent: str
    specialist: str | None            # None ⇒ answered without a specialist (meta/chitchat/out_of_scope)
    allowed_tools: tuple[str, ...]    # the exact tool names this specialist may run (allow-list)
    retrieval: RetrievalRequirement
    needs_live_data: bool
    max_tool_calls: int
    max_retrieval_calls: int
    allows_answer_model: bool         # False ⇒ no answer-model call (out_of_scope refusal)
    timeout_ms: int
    assumptions: tuple[str, ...] = ()
    plan_version: str = PLAN_VERSION

    def to_trace_record(self) -> dict:
        return {
            "intent": self.intent,
            "specialist": self.specialist,
            "allowed_tools": self.allowed_tools,
            "retrieval": self.retrieval.value,
            "needs_live_data": self.needs_live_data,
            "max_tool_calls": self.max_tool_calls,
            "max_retrieval_calls": self.max_retrieval_calls,
            "allows_answer_model": self.allows_answer_model,
            "timeout_ms": self.timeout_ms,
            "assumptions": self.assumptions,
            "plan_version": self.plan_version,
        }


def _freeze_meta(meta: Mapping | tuple) -> tuple[tuple[str, Scalar], ...]:
    """Normalize a caller's step metadata into an immutable, hashable, deterministic
    tuple of (key, scalar) pairs. Accepts a dict (natural at call sites) or an
    already-frozen pair tuple. Rejects non-scalar values so a document body or a raw
    payload can never enter a trace record."""
    items = meta.items() if isinstance(meta, Mapping) else meta
    frozen: list[tuple[str, Scalar]] = []
    for key, value in items:
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise TypeError(
                f"StepResult.meta[{key!r}] must be a trace-safe scalar, got {type(value).__name__}"
            )
        frozen.append((str(key), value))
    return tuple(sorted(frozen))


@dataclass(frozen=True)
class StepResult:
    """One execution step's record — a retrieval or a single tool invocation
    (canonical §6.3). Carries only trace-safe metadata, never payload.

    `meta` is stored as an immutable, sorted tuple of (key, scalar) pairs so the
    record is fully hashable and cannot be mutated after construction — a frozen
    dataclass alone would still expose a mutable dict. Read it back with
    `meta_map`."""
    kind: str                         # "retrieval" | "tool"
    name: str                         # strategy name or tool name
    status: str                       # "ok" | "error" | "no_record" | "degraded" | ...
    latency_ms: int
    error_code: str | None = None
    degraded: bool = False            # True when a failure policy downgraded this step
    meta: tuple[tuple[str, Scalar], ...] = ()  # ids/scores/counts — no document body

    def __post_init__(self) -> None:
        # Normalize whatever the caller passed (dict or pairs) into the frozen form.
        # object.__setattr__ because the dataclass is frozen (normal assignment raises).
        object.__setattr__(self, "meta", _freeze_meta(self.meta))

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def meta_map(self) -> dict:
        """A fresh dict view of the frozen meta pairs, for convenient reads."""
        return dict(self.meta)

    def to_trace_record(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "degraded": self.degraded,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class ValidationOutcome:
    """A validator's verdict plus the deterministic action to take (canonical §6.4).
    `action` drives the failure-policy state machine (§4.3); `code`/`reason` are
    short, human-readable, never a payload."""
    valid: bool
    action: ValidationAction
    code: str | None = None
    reason: str | None = None

    @classmethod
    def proceed(cls) -> ValidationOutcome:
        return cls(valid=True, action=ValidationAction.CONTINUE)

    @classmethod
    def halt(cls, action: ValidationAction, code: str, reason: str) -> ValidationOutcome:
        """A non-continue verdict — retry / partial / block / stop."""
        return cls(valid=False, action=action, code=code, reason=reason)


@dataclass(frozen=True)
class SourceRef:
    """A single cited-source reference — stable ids only, no document body (canonical
    §6.5). `chunk_id`/`page`/`viewer_available` support the (should-ship) evidence
    viewer and default empty so the must-ship path can cite with just doc + file."""
    document_id: str
    file: str
    chunk_id: str = ""
    section: str | None = None
    page: int | None = None
    source_type: str | None = None
    viewer_available: bool = False

    def to_trace_record(self) -> dict:
        return {
            "document_id": self.document_id,
            "file": self.file,
            "chunk_id": self.chunk_id,
            "section": self.section,
            "page": self.page,
            "source_type": self.source_type,
            "viewer_available": self.viewer_available,
        }


@dataclass(frozen=True)
class RequestOutcome:
    """The single typed terminal outcome of a request (canonical §6.5). `status` maps
    the internal terminal state onto the client-facing envelope status. `answer` is
    sensitive and excluded from `to_trace_record()`; only source ids survive."""
    terminal_state: TerminalState
    answer: str
    sources: tuple[SourceRef, ...]
    latency_ms: int

    @property
    def status(self) -> str:
        return _ENVELOPE_STATUSES.get(self.terminal_state, "error")

    def to_trace_record(self) -> dict:
        return {
            "terminal_state": self.terminal_state.value,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "sources": [s.to_trace_record() for s in self.sources],
        }
