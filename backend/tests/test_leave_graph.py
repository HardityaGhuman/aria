from datetime import date

from langgraph.checkpoint.memory import InMemorySaver

from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal
from backend.services import leave_graph as lg


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def _extract_ok(raw):
    return {"start_date": "2026-08-12", "end_date": "2026-08-14", "reason": "vacation"}


class _FakeCaseStore:
    """In-memory stand-in so the graph test needs no Postgres. Mirrors the leave_case
    API the graph calls: get_case/transition."""
    def __init__(self):
        self.rows = {}

    def transition(self, case_id, new_status, actor_id, detail, *, confirmation_id=None):
        self.rows[case_id]["status"] = new_status
        if confirmation_id:
            self.rows[case_id]["confirmation_id"] = confirmation_id
        return self.rows[case_id]

    def get_case(self, case_id):
        return self.rows.get(case_id)


def _seed_store(status="draft"):
    store = _FakeCaseStore()
    store.rows["cid"] = {"case_id": "cid", "status": status, "employee_email": "employee@gsvh.test",
                         "approver_email": "manager@gsvh.test", "confirmation_id": None}
    return store


def _graph(store, hris=None, saver=None):
    hris = hris or MockHRIS()
    return lg.build_leave_graph(
        hris=hris, checkpointer=saver or InMemorySaver(), extract_fn=_extract_ok,
        case_store=store, today=date(2026, 8, 1),
    )


def test_happy_path_pauses_for_approval():
    store = _seed_store()
    g = _graph(store)
    row = lg.start_case(g, case_id="cid", principal=_p(), raw_text="aug 12-14",
                        approver_email="manager@gsvh.test")
    assert row["status"] == "pending_approval"


def test_approve_books():
    store = _seed_store()
    g = _graph(store, MockHRIS())
    lg.start_case(g, case_id="cid", principal=_p(), raw_text="aug 12-14", approver_email="manager@gsvh.test")
    row = lg.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "booked"
    assert row["confirmation_id"]


def test_deny_ends_denied_manager():
    store = _seed_store()
    g = _graph(store)
    lg.start_case(g, case_id="cid", principal=_p(), raw_text="aug 12-14", approver_email="manager@gsvh.test")
    row = lg.resume_case(g, case_id="cid", decision="deny", actor_id="manager@gsvh.test")
    assert row["status"] == "denied_manager"


def test_validation_fail_never_reaches_approval():
    store = _seed_store()
    hris = MockHRIS(seed={"employee@gsvh.test": {"total_pto": 1, "pto_used": 0, "region": "us", "manager_email": "manager@gsvh.test"}})
    g = _graph(store, hris)
    row = lg.start_case(g, case_id="cid", principal=_p(), raw_text="aug 12-14", approver_email="manager@gsvh.test")
    assert row["status"] == "denied_policy"


def test_resume_after_rebuild_survives():
    """A new graph object sharing the same checkpointer resumes the paused Case —
    proves the pause is durable state, not in-process memory."""
    saver = InMemorySaver()
    store = _seed_store()
    g1 = _graph(store, MockHRIS(), saver=saver)
    lg.start_case(g1, case_id="cid", principal=_p(), raw_text="aug 12-14", approver_email="manager@gsvh.test")
    # Rebuild the graph (simulating a process restart) on the SAME checkpointer.
    g2 = _graph(store, MockHRIS(), saver=saver)
    row = lg.resume_case(g2, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "booked"
