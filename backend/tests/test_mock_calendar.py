"""The mock Calendar is the bounded read-only source for the team's out-of-office
view (v1). It returns the currently-away colleagues as typed rows (name + return
date); a real Google CalendarClient is a later drop-in behind the same Protocol.
Nothing about OOO is persisted in our schema — the tool reads a transient view."""
from backend.core.calendar.mock import MockCalendar
from backend.core.tools.principal import Principal


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def test_returns_seeded_out_of_office_rows():
    out = MockCalendar().whos_out(_p())
    assert isinstance(out, list) and len(out) >= 1
    for row in out:
        assert isinstance(row["name"], str) and row["name"]
        assert isinstance(row["until"], str) and row["until"]


def test_empty_seed_returns_empty_list():
    assert MockCalendar(seed=[]).whos_out(_p()) == []


def test_rows_are_copied_not_shared():
    cal = MockCalendar()
    first = cal.whos_out(_p())
    first[0]["name"] = "MUTATED"
    # A mutation of the returned rows must not bleed into the seed.
    assert cal.whos_out(_p())[0]["name"] != "MUTATED"
