"""core/write/errors.py
--------------------
The write-boundary failure taxonomy. Two questions only: may we retry this, and
may we retry it safely?

  transient — the write may not have happened, and the same call may work later
              (timeout, 5xx, rate limit, connection reset). Retryable BECAUSE
              every write tool is idempotent by case_id.
  permanent — the write will never succeed as-is (4xx, unknown resource, auth
              rejected, bad request). Retrying only burns budget.

An exception type we do not recognise is classified PERMANENT. That is a
deliberate fail-closed choice: an unknown error may be a partially-applied write,
and retrying it could double-write. Unknown => stop and escalate to a human."""


# Connectors raise these explicitly when they know which kind of failure occurred.
class TransientWriteError(Exception):
    """The write may be retried; the connector was unavailable, not unwilling."""


class PermanentWriteError(Exception):
    """The write must not be retried; the connector refused the request itself."""


# Builtins whose semantics are unambiguously "the network, not the request".
_TRANSIENT_BUILTINS = (TimeoutError, ConnectionError)


def classify_write_error(exc: Exception) -> str:
    """-> "transient" | "permanent". Unknown exception types are permanent."""
    if isinstance(exc, TransientWriteError):
        return "transient"
    if isinstance(exc, PermanentWriteError):
        return "permanent"
    if isinstance(exc, _TRANSIENT_BUILTINS):
        return "transient"
    return "permanent"
