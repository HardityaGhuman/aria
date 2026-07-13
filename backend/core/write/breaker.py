"""core/write/breaker.py
---------------------
Prevent repeated harm. When a connector is flapping, every Case that reaches the
write boundary burns its whole retry budget before dead-lettering — noisy, slow,
and pointless. The breaker short-circuits that: after `threshold` CONSECUTIVE
transient failures it opens, and an open breaker means the write node does not
call the connector at all; the Case goes straight to dead_letter, where a human
can replay it once the connector is healthy. Automation halts, work is preserved.

Reset is EXPLICIT (an admin route) — never time-based. A breaker that heals itself
quietly re-enters the outage it exists to surface, and nobody ever learns about it.

KNOWN LIMIT (documented, not hidden): this is process-local in-memory state. With
one API process it is exactly right. At N replicas each holds its own counter, so
the effective threshold is N x threshold. The fix (a `write_breaker` table with a
version column, so the counter is shared) is a follow-up, not this slice."""
from backend.core.config import WRITE_BREAKER_THRESHOLD


class CircuitBreaker:
    def __init__(self, name: str, threshold: int = WRITE_BREAKER_THRESHOLD) -> None:
        self.name = name
        self.threshold = threshold
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def is_open(self) -> bool:
        return self.consecutive_failures >= self.threshold

    def reset(self) -> None:
        self.consecutive_failures = 0


# One breaker per connector, keyed by name ("access-provisioner").
_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(name: str) -> CircuitBreaker:
    breaker = _BREAKERS.get(name)
    if breaker is None:
        breaker = CircuitBreaker(name)
        _BREAKERS[name] = breaker
    return breaker


def reset_breaker(name: str) -> None:
    """Explicit clearance (admin route, or a test isolating itself)."""
    _BREAKERS.pop(name, None)
