"""core/access — the access-provisioning seam.

`AccessProvisioner` is the interface the onboarding agent writes through; the IdP
(Okta/SCIM, later) is the system of record for a grant. We never persist grants
here — same ownership line as HRIS and Jira: the app owns the assistant's state,
never the business's truth. v1 backend is the local seeded mock (mock.py)."""
from typing import Protocol, runtime_checkable

from backend.core.tools.principal import Principal


@runtime_checkable
class AccessProvisioner(Protocol):
    def grant(self, principal: Principal, case_id: str, tools: list[str]) -> dict:
        """Grant `tools` to the caller. Returns {"grant_id": str, "tools": list[str]}.

        Idempotent by `case_id` — a retried or replayed attempt returns the SAME
        grant_id and never grants twice. Raises PermanentWriteError for an
        off-catalog tool (the request is wrong and always will be) and
        TransientWriteError when the upstream is unavailable (retryable)."""
        ...
