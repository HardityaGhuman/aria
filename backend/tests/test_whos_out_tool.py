"""whos_out is a thin CalendarClient adapter: a read-only team out-of-office view.
No arguments, no confirmation, min_role employee. Its summary is TEMPLATE-built from
typed fields (name + date) — never raw external text spliced in with authority (S2)."""
from backend.core.tools.principal import Principal
from backend.core.tools.whos_out import WhosOutTool


class _FakeCalendar:
    def __init__(self, rows):
        self.rows = rows

    def whos_out(self, principal):
        return list(self.rows)


ALICE = Principal(user_id=1, email="alice@x.test", role="employee", region="us")


def _tool(rows):
    return WhosOutTool(_FakeCalendar(rows))


def test_lists_people_out_with_return_dates():
    result = _tool([
        {"name": "Priya", "until": "2026-07-10"},
        {"name": "Marcus", "until": "2026-07-08"},
    ]).invoke({}, ALICE)
    assert result.status == "ok"
    assert result.data["out"][0]["name"] == "Priya"
    assert "Priya" in result.summary and "2026-07-10" in result.summary
    assert "Marcus" in result.summary


def test_nobody_out_is_a_clean_result():
    result = _tool([]).invoke({}, ALICE)
    assert result.status == "ok"
    assert result.data["out"] == []
    assert "No one" in result.summary or "no one" in result.summary


def test_ignores_injected_args_and_reads_calendar():
    # Adversarial args must not change what the read returns.
    result = _tool([{"name": "Priya", "until": "2026-07-10"}]).invoke(
        {"email": "boss@x.test", "name": "hacked"}, ALICE
    )
    assert result.data["out"][0]["name"] == "Priya"


def test_tool_metadata():
    t = _tool([])
    assert t.name == "whos_out"
    assert t.requires_confirmation is False
    assert t.min_role == "employee"
    assert t.parameters.get("properties") == {}  # no args — team-wide read
