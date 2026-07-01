"""core/hris/mock.py
-------------------
A local, seeded, in-memory HRIS — the v1 system of record for leave. Chosen over a
live Google Sheet so a demo runs with no service-account setup, no network/quota on
the critical path, and is fast + unit-testable. It is a distinct bounded module the
middleware calls, NOT leave tables in the chat schema — the ownership line holds."""
from backend.core.tools.principal import Principal

# Seeded HRIS rows, keyed by the caller's email (the stable identity the Principal
# carries). manager_email is here for Phase C approval routing — unused in Unit 2.
_SEED = {
    "hr@gsvh.test": {"total_pto": 26, "pto_used": 4, "region": "us", "manager_email": None},
    "manager@gsvh.test": {"total_pto": 24, "pto_used": 10, "region": "us", "manager_email": "hr@gsvh.test"},
    "employee@gsvh.test": {"total_pto": 20, "pto_used": 8, "region": "us", "manager_email": "manager@gsvh.test"},
    "employee2@gsvh.test": {"total_pto": 22, "pto_used": 2, "region": "india", "manager_email": "manager@gsvh.test"},
}


class MockHRIS:
    def __init__(self, seed: dict | None = None) -> None:
        # Copy so a test mutating rows can't bleed into another test.
        source = _SEED if seed is None else seed
        self._rows = {email: dict(row) for email, row in source.items()}

    def get_balance(self, principal: Principal) -> dict | None:
        row = self._rows.get(principal.email) if principal.email else None
        if row is None:
            return None
        total = row["total_pto"]
        used = row["pto_used"]
        return {"remaining": total - used, "total": total, "used": used}
