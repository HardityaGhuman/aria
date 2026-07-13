"""MockAccessProvisioner: the idempotency ledger is the safety property — a retried
or replayed attempt returns the SAME grant_id and never grants twice. Failure
injection is how the graph tests drive the retry / dead-letter paths offline."""
import pytest

from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.principal import Principal
from backend.core.write.errors import PermanentWriteError, TransientWriteError


def _p():
    return Principal(user_id=1, email="newhire@gsvh.test", role="employee", region="us")


def test_grant_returns_grant_id_and_tools():
    out = MockAccessProvisioner().grant(_p(), "cid", ["github", "slack"])
    assert out["grant_id"]
    assert out["tools"] == ["github", "slack"]


def test_idempotent_by_case_id_never_double_grants():
    prov = MockAccessProvisioner()
    first = prov.grant(_p(), "cid", ["github"])
    second = prov.grant(_p(), "cid", ["github"])
    assert first["grant_id"] == second["grant_id"]
    assert len(prov.grants) == 1


def test_off_catalog_tool_is_permanent():
    with pytest.raises(PermanentWriteError):
        MockAccessProvisioner().grant(_p(), "cid", ["prod-root"])


def test_fail_times_injects_transient_then_succeeds():
    prov = MockAccessProvisioner(fail_times=1, fail_with=TransientWriteError)
    with pytest.raises(TransientWriteError):
        prov.grant(_p(), "cid", ["github"])
    out = prov.grant(_p(), "cid", ["github"])   # second attempt succeeds
    assert out["grant_id"]
    assert prov.calls == 2
