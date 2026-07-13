"""Circuit breaker: opens after N CONSECUTIVE transient failures, a success resets
the count, and clearance is explicit (never time-based — a self-healing breaker
hides the outage it exists to surface)."""
from backend.core.write.breaker import CircuitBreaker, get_breaker, reset_breaker


def test_closed_when_fresh():
    assert CircuitBreaker("x", threshold=3).is_open() is False


def test_opens_at_threshold():
    b = CircuitBreaker("x", threshold=3)
    b.record_failure()
    b.record_failure()
    assert b.is_open() is False
    b.record_failure()
    assert b.is_open() is True


def test_success_resets_the_counter():
    b = CircuitBreaker("x", threshold=3)
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    b.record_failure()
    assert b.is_open() is False       # the streak was broken; not 4-in-a-row


def test_open_breaker_stays_open_until_explicit_reset():
    b = CircuitBreaker("x", threshold=2)
    b.record_failure()
    b.record_failure()
    assert b.is_open() is True
    b.reset()
    assert b.is_open() is False
    assert b.consecutive_failures == 0


def test_registry_returns_same_breaker_per_name():
    reset_breaker("conn-a")
    a1 = get_breaker("conn-a")
    a2 = get_breaker("conn-a")
    b = get_breaker("conn-b")
    assert a1 is a2
    assert a1 is not b


def test_reset_breaker_clears_registry_instance():
    reset_breaker("conn-c")
    b = get_breaker("conn-c")
    for _ in range(b.threshold):
        b.record_failure()
    assert b.is_open() is True
    reset_breaker("conn-c")
    assert get_breaker("conn-c").is_open() is False
