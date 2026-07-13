"""core/write/node.py
------------------
THE write boundary. One node, shared by every write agent — because the alternative,
proven by this codebase, is that two of three agents quietly ship without a retry, a
breaker, or a DLQ, and nobody notices until an approved Case dies on a 503.

The node is re-entered by the retry EDGE (never a Python loop), so every attempt is its
own checkpoint and a process restart mid-retry loses nothing. Everything here is
therefore idempotent: the approve-transition is status-guarded, and every write tool is
idempotent by case_id.

The node writes the Case row ONLY on success. Every failure path just sets
`failure_class` and lets the router pick the terminal node — exactly one node writes each
terminal status, because two writers is how a state machine starts lying."""
import time
from collections.abc import Callable
from dataclasses import dataclass

# pyrefly: ignore [missing-import]
from langgraph.graph import END

from backend.core.tools.base import ToolResult
from backend.core.tools.principal import Principal
from backend.core.write import case_store as default_store
from backend.core.write import trace as wt
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.case_store import CaseSpec
from backend.core.write.errors import classify_write_error

IDENTITY_GONE = "requester no longer exists"


@dataclass(frozen=True)
class WriteSpec:
    connector: str                                        # breaker key + trace field
    invoke: Callable[[dict, Principal], ToolResult]       # call the tool with the state
    verify: Callable[[ToolResult, dict], bool]            # execution != correctness
    success_status: str
    result_field: str


def make_write_node(write_spec: WriteSpec, case_spec: CaseSpec, *, breaker: CircuitBreaker,
                    principal_loader, store=default_store):
    def write(state: dict) -> dict:
        started = time.perf_counter()
        case_id = state["case_id"]
        wt.case_node_started(case_id, "write")

        # Idempotent: the retry edge re-enters this node, and a resumed Case re-runs it.
        current = store.get_case(case_spec, case_id)
        if current and current["status"] == "pending_approval":
            store.transition(case_spec, case_id, "approved",
                             state.get("decision_actor") or "manager", "approved")

        attempt = state.get("attempt", 0) + 1
        past_errors = list(state.get("past_errors") or [])

        def failed(failure_class: str, note: str, *, latency: int | None = None) -> dict:
            ms = latency if latency is not None else int((time.perf_counter() - started) * 1000)
            wt.case_node_completed(case_id, "write", "failed", ms)
            return {"failure_class": failure_class, "attempt": attempt,
                    "past_errors": past_errors + [note]}

        # Identity is reloaded HERE, after the human sleep: never write under the role or
        # region the requester held when the Case was filed. Gone => fail closed.
        principal = principal_loader(state["user_id"])
        if principal is None:
            return failed("permanent", IDENTITY_GONE)

        # Breaker open => do not touch the connector at all. The Case goes to the DLQ,
        # where a human can replay it once the connector is healthy. Automation halts;
        # work is preserved.
        if breaker.is_open():
            wt.case_write_result(case_id, attempt, "skipped", latency_ms=0,
                                 failure_class="breaker_open")
            return failed("breaker_open", "breaker_open")

        wt.case_write_attempted(case_id, attempt, write_spec.connector)
        try:
            result = write_spec.invoke(state, principal)
        except Exception as exc:                    # the ONLY place a failure is classified
            failure_class = classify_write_error(exc)     # unknown => permanent, fail closed
            latency = int((time.perf_counter() - started) * 1000)
            wt.case_write_result(case_id, attempt, "failed", latency_ms=latency,
                                 failure_class=failure_class)
            if failure_class == "transient":
                breaker.record_failure()
            return failed(failure_class, f"{failure_class}: {type(exc).__name__}", latency=latency)

        # EXECUTION IS NOT CORRECTNESS. The connector not raising is a mechanical fact, not
        # a success. Verify the payload IS what the human approved — a connector answering
        # "200 OK" with an empty or partial body is the canonical silent failure, and
        # recording it as success writes a lie into the audit log. Permanent: a connector
        # that mis-answers will mis-answer again; a human must look at it.
        latency = int((time.perf_counter() - started) * 1000)
        if not write_spec.verify(result, state):
            breaker.record_success()               # the connector RESPONDED; it is not flapping
            wt.case_write_result(case_id, attempt, "unverified", latency_ms=latency,
                                 failure_class="permanent")
            return failed("permanent", "permanent: result did not match the approved request",
                          latency=latency)

        breaker.record_success()
        wt.case_write_result(case_id, attempt, "ok", latency_ms=latency)
        result_id = (result.data or {})[write_spec.result_field]
        store.transition(case_spec, case_id, write_spec.success_status, "system",
                         write_spec.success_status,
                         **{case_spec.result_column: result_id, "attempt": attempt})
        wt.case_node_completed(case_id, "write", write_spec.success_status, latency)
        return {"status": write_spec.success_status, write_spec.result_field: result_id,
                "attempt": attempt, "failure_class": None}

    return write


def make_write_router(max_attempts: int):
    """Pure, exhaustive, and in Python — the retry decision never goes near a model.

    Contract with the node: it writes the row ONLY on success. Every failure path leaves
    `status` unset and sets `failure_class`, so this router alone decides stop-vs-retry."""
    def after_write(state: dict) -> str:
        if state.get("status") and state.get("failure_class") is None:
            return END                                  # the node already wrote the row
        failure_class = state.get("failure_class")
        if failure_class == "permanent":
            return "write_failed"                       # never retry what will never work
        if failure_class == "breaker_open":
            return "dead_letter"                        # connector is out; don't spend budget
        if state.get("attempt", 0) >= max_attempts:
            return "dead_letter"                        # transient, budget spent
        return "write"                                  # transient, budget left -> retry EDGE

    return after_write


class _StoreAdapter:
    """The graphs inject a module-shaped store (`get_case(case_id)`), while the engine is
    spec-first (`get_case(spec, case_id)`). This adapter bridges them in one place, so the
    three graphs keep their existing injection point and every test that passes a fake
    store keeps working unchanged."""

    def __init__(self, module) -> None:
        self._module = module

    def get_case(self, spec, case_id):
        return self._module.get_case(case_id)

    def transition(self, spec, case_id, new_status, actor_id, detail, **result):
        return self._module.transition(case_id, new_status, actor_id, detail, **result)


def adapt_store(module) -> _StoreAdapter:
    return _StoreAdapter(module)


def make_terminal_nodes(case_spec: CaseSpec, store=default_store):
    """The two ways a write ends badly. Kept apart on purpose: `write_failed` is terminal
    (the connector refused, and will refuse again — a human files a new Case), while
    `dead_letter` keeps its checkpoint and is replayable (the connector was down, not
    unwilling)."""
    def write_failed(state: dict) -> dict:
        reason = (state.get("past_errors") or ["write failed"])[-1]
        store.transition(case_spec, state["case_id"], "write_failed", "system", reason,
                         attempt=state.get("attempt"), failure_reason="permanent")
        return {"status": "write_failed"}

    def dead_letter(state: dict) -> dict:
        failure_class = state.get("failure_class") or "transient"
        reason = (state.get("past_errors") or ["transient failure"])[-1]
        store.transition(case_spec, state["case_id"], "dead_letter", "system", reason,
                         attempt=state.get("attempt"), failure_reason=failure_class)
        return {"status": "dead_letter"}

    return write_failed, dead_letter
