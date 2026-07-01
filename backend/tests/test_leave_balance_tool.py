"""leave_balance is a thin HRIS adapter. Identity is the Principal — an injected
email in args is ignored (the caller can only ever read their OWN balance)."""
from backend.core.tools.leave_balance import LeaveBalanceTool
from backend.core.tools.principal import Principal


class _FakeHRIS:
    def __init__(self, table):
        self.table = table

    def get_balance(self, principal):
        return self.table.get(principal.email)


ALICE = Principal(user_id=1, email="alice@x.test", role="employee", region="us")
BOB = Principal(user_id=2, email="bob@x.test", role="employee", region="us")


def _tool():
    return LeaveBalanceTool(_FakeHRIS({
        "alice@x.test": {"remaining": 12, "total": 20, "used": 8},
        "bob@x.test": {"remaining": 3, "total": 20, "used": 17},
    }))


def test_returns_caller_balance():
    result = _tool().invoke({}, ALICE)
    assert result.status == "ok"
    assert result.data["remaining"] == 12


def test_ignores_injected_email_and_reads_principal():
    # Adversarial: args say bob, but alice is the Principal → alice's balance only.
    result = _tool().invoke({"email": "bob@x.test"}, ALICE)
    assert result.data["remaining"] == 12  # alice's, not bob's 3


def test_no_hris_record_is_a_clean_result():
    result = _tool().invoke({}, Principal(user_id=9, email="ghost@x.test", role="employee", region="us"))
    assert result.status == "ok"
    assert result.data["remaining"] is None


def test_tool_metadata():
    t = _tool()
    assert t.name == "leave_balance"
    assert t.requires_confirmation is False
    assert t.min_role == "employee"
    assert t.parameters.get("properties") == {}  # no args — identity is the Principal
