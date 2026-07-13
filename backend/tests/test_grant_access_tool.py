"""GrantAccessTool: identity is the server Principal (never an arg), idempotent by
case_id, and — unlike the other write tools — connector exceptions PROPAGATE, because
only the graph may classify a failure as transient/permanent."""
import pytest

from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.grant_access import GrantAccessTool
from backend.core.tools.principal import Principal
from backend.core.write.errors import TransientWriteError


def _p():
    return Principal(user_id=1, email="newhire@gsvh.test", role="employee", region="us")


def test_invoke_grants_and_returns_grant_id():
    tool = GrantAccessTool(MockAccessProvisioner())
    result = tool.invoke({"case_id": "cid", "tools": ["github", "slack"]}, _p())
    assert result.status == "ok"
    assert result.data["grant_id"]
    assert result.data["tools"] == ["github", "slack"]


def test_replayed_invoke_is_idempotent():
    prov = MockAccessProvisioner()
    tool = GrantAccessTool(prov)
    a = tool.invoke({"case_id": "cid", "tools": ["github"]}, _p())
    b = tool.invoke({"case_id": "cid", "tools": ["github"]}, _p())
    assert a.data["grant_id"] == b.data["grant_id"]
    assert len(prov.grants) == 1


def test_connector_error_propagates_for_the_graph_to_classify():
    tool = GrantAccessTool(MockAccessProvisioner(fail_times=1, fail_with=TransientWriteError))
    with pytest.raises(TransientWriteError):
        tool.invoke({"case_id": "cid", "tools": ["github"]}, _p())


def test_min_role_is_employee():
    assert GrantAccessTool(MockAccessProvisioner()).min_role == "employee"
