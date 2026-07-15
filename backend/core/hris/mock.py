"""core/hris/mock.py
-------------------
A local, seeded, in-memory HRIS — the v1 system of record for leave. Now stateful:
``submit_leave`` books leave and decrements the balance, so a later ``get_balance``
reflects it. Booking is idempotent by ``case_id`` (a retried approve-click never
double-decrements). Still a bounded module behind ``HRISClient``, NOT chat tables —
the ownership line holds."""
import uuid

from backend.core.tools.principal import Principal

# Seeded HRIS rows, keyed by the caller's email (the stable identity the Principal
# carries). manager_email routes the approval to the caller's manager.
_SEED = {
    "hr@gsvh.test": {"total_pto": 26, "pto_used": 4, "region": "us", "manager_email": None},
    "manager@gsvh.test": {"total_pto": 24, "pto_used": 10, "region": "us", "manager_email": "hr@gsvh.test"},
    "employee@gsvh.test": {"total_pto": 20, "pto_used": 8, "region": "us", "manager_email": "manager@gsvh.test"},
    "employee2@gsvh.test": {"total_pto": 22, "pto_used": 2, "region": "india", "manager_email": "manager@gsvh.test"},
}


class MockHRIS:
    def __init__(self, seed: dict | None = None, fail_times: int = 0,
                 fail_with: type[Exception] | None = None) -> None:
        # Copy so a test mutating rows can't bleed into another test.
        source = _SEED if seed is None else seed
        self._rows = {email: dict(row) for email, row in source.items()}
        # case_id -> {"confirmation_id", "email", "days"} — idempotency ledger.
        self._bookings: dict[str, dict] = {}
        # Failure injection (mirrors MockAccessProvisioner): lets the graph tests drive
        # transient-then-success, budget exhaustion, and breaker-open with no network.
        self._fail_remaining = fail_times
        self._fail_with = fail_with

    def get_balance(self, principal: Principal) -> dict | None:
        row = self._rows.get(principal.email) if principal.email else None
        if row is None:
            return None
        total = row["total_pto"]
        used = row["pto_used"]
        return {"remaining": total - used, "total": total, "used": used}

    def manager_email(self, principal: Principal) -> str | None:
        row = self._rows.get(principal.email) if principal.email else None
        return row.get("manager_email") if row else None

    def submit_leave(
        self, principal: Principal, case_id: str, start_date: str, end_date: str, days: int
    ) -> dict:
        # Injection runs AHEAD of the idempotency check: a connector that fails before it
        # commits is exactly the transient case the retry edge exists for.
        if self._fail_remaining > 0 and self._fail_with is not None:
            self._fail_remaining -= 1
            raise self._fail_with(f"injected failure for case {case_id}")

        # Idempotent replay: same case_id -> same confirmation, no second decrement.
        # The echo is part of the replay too: the graph verifies the payload on EVERY
        # attempt, so a replay must be able to prove what it booked just like a first try.
        prior = self._bookings.get(case_id)
        if prior is not None:
            row = self._rows[prior["email"]]
            return {"confirmation_id": prior["confirmation_id"],
                    "remaining": row["total_pto"] - row["pto_used"],
                    "start_date": prior["start_date"], "end_date": prior["end_date"],
                    "days": prior["days"]}

        row = self._rows.get(principal.email) if principal.email else None
        if row is None:
            raise KeyError(f"no HRIS record for {principal.email!r}")

        row["pto_used"] += days
        confirmation_id = f"BK-{uuid.uuid4().hex[:10].upper()}"
        self._bookings[case_id] = {
            "confirmation_id": confirmation_id, "email": principal.email, "days": days,
            "start_date": start_date, "end_date": end_date,
        }
        # The connector ECHOES what it actually booked. Without the echo the graph cannot
        # check execution against correctness — it would have to take "no exception" as
        # proof, which is exactly the silent failure the verify step exists to catch.
        return {"confirmation_id": confirmation_id, "remaining": row["total_pto"] - row["pto_used"],
                "start_date": start_date, "end_date": end_date, "days": days}
