"""services/leave_validator.py
----------------------------
Deterministic pre-draft gate. No LLM anywhere in here: every check is arithmetic on
dates and the HRIS balance, so the same request always yields the same verdict and is
fully unit-testable. Checks run in a fixed order and short-circuit on the first
failure with a short, user-safe reason. Balance sufficiency is the last (and most
important) gate — a request that clears notice/length/blackout but exceeds the
remaining balance is denied here, before any approval is ever requested."""
from dataclasses import dataclass
from datetime import date

from backend.core.config import (
    LEAVE_BLACKOUT_DATES,
    LEAVE_MAX_CONSECUTIVE_DAYS,
    LEAVE_MIN_NOTICE_DAYS,
)
from backend.core.hris import HRISClient
from backend.core.tools.principal import Principal


@dataclass
class ValidationResult:
    ok: bool
    days: int
    reason: str | None


def compute_days(start_date: str, end_date: str) -> int:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end date is before start date")
    return (end - start).days + 1


def _overlaps_blackout(start: date, end: date) -> bool:
    for raw in LEAVE_BLACKOUT_DATES:
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            continue
        if start <= d <= end:
            return True
    return False


def validate_leave(principal: Principal, hris: HRISClient, start_date: str, end_date: str, *, today: date | None = None) -> ValidationResult:
    today = today or date.today()
    # 1. parse + ordering
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return ValidationResult(False, 0, "Could not read the leave dates.")
    if end < start:
        return ValidationResult(False, 0, "The end date is before the start date.")
    days = (end - start).days + 1

    # 2. minimum notice
    if (start - today).days < LEAVE_MIN_NOTICE_DAYS:
        return ValidationResult(False, days, f"Leave needs at least {LEAVE_MIN_NOTICE_DAYS} days' notice.")

    # 3. maximum consecutive days
    if days > LEAVE_MAX_CONSECUTIVE_DAYS:
        return ValidationResult(False, days, f"Leave cannot exceed {LEAVE_MAX_CONSECUTIVE_DAYS} consecutive days.")

    # 4. blackout overlap
    if _overlaps_blackout(start, end):
        return ValidationResult(False, days, "The requested dates overlap a blackout period.")

    # 5. balance sufficiency (fail closed if the caller has no HRIS record)
    balance = hris.get_balance(principal)
    if balance is None:
        return ValidationResult(False, days, "No HRIS leave record was found for you.")
    if days > balance["remaining"]:
        return ValidationResult(False, days, f"Insufficient balance: {days} requested, {balance['remaining']} remaining.")

    return ValidationResult(True, days, None)
