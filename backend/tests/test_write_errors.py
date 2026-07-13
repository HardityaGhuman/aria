"""Write-boundary failure taxonomy. Fail closed: anything we don't understand is
PERMANENT, because retrying an error of unknown shape can double-write."""
import pytest

from backend.core.write.errors import (
    PermanentWriteError,
    TransientWriteError,
    classify_write_error,
)


@pytest.mark.parametrize("exc", [
    TransientWriteError("upstream 503"),
    TimeoutError("read timed out"),
    ConnectionError("connection reset"),
    ConnectionResetError("peer reset"),
])
def test_transient_family(exc):
    assert classify_write_error(exc) == "transient"


@pytest.mark.parametrize("exc", [
    PermanentWriteError("unknown tool at connector"),
    KeyError("no such user"),
    ValueError("bad request"),
])
def test_permanent_family(exc):
    assert classify_write_error(exc) == "permanent"


def test_unknown_exception_is_permanent_fail_closed():
    class WeirdError(Exception):
        pass
    assert classify_write_error(WeirdError("???")) == "permanent"
