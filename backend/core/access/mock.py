"""core/access/mock.py
-------------------
A local, in-memory IdP — the v1 system of record for access grants, behind the
AccessProvisioner seam. Two jobs:

1. IDEMPOTENCY BY case_id. The ledger (`grants`) is what makes the graph's retry
   edge safe: attempt 2 of the same Case returns attempt 1's grant_id, so a
   flapping connector can never produce two grants for one approval. This is the
   single highest-consequence invariant in the slice.

2. FAILURE INJECTION (`fail_times` / `fail_with`). The graph tests need to drive
   transient-then-success, budget exhaustion, and permanent failure with no
   network and no sleep. `calls` counts real entries into the connector, so a test
   can assert the breaker actually SHORT-CIRCUITED the write rather than merely
   swallowing its error."""
import uuid

from backend.core.access.catalog import TOOL_CATALOG
from backend.core.tools.principal import Principal
from backend.core.write.errors import PermanentWriteError


class MockAccessProvisioner:
    def __init__(self, fail_times: int = 0, fail_with: type[Exception] | None = None) -> None:
        self._fail_remaining = fail_times
        self._fail_with = fail_with
        self.calls = 0
        # case_id -> {"grant_id", "tools"} — the idempotency ledger.
        self.grants: dict[str, dict] = {}

    def grant(self, principal: Principal, case_id: str, tools: list[str]) -> dict:
        self.calls += 1

        # Idempotent replay: same case_id -> same grant, nothing new provisioned.
        prior = self.grants.get(case_id)
        if prior is not None:
            return dict(prior)

        if self._fail_remaining > 0 and self._fail_with is not None:
            self._fail_remaining -= 1
            raise self._fail_with(f"injected failure for case {case_id}")

        unknown = [t for t in tools if t not in TOOL_CATALOG]
        if unknown:
            # The request itself is wrong and always will be -> never retry it.
            raise PermanentWriteError(f"unknown tool(s): {', '.join(unknown)}")

        out = {"grant_id": f"grant-{uuid.uuid4().hex[:12]}", "tools": list(tools)}
        self.grants[case_id] = out
        return dict(out)
