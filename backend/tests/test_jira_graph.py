"""Jira write graph: pauses for approval, creates on approve, denies on deny,
validation short-circuits, and the pause survives a graph rebuild (durable resume)."""
from langgraph.checkpoint.memory import InMemorySaver

from backend.core.jira.mock import MockJira
from backend.core.tools.principal import Principal
from backend.services import jira_graph as jg


def _p(email="employee@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def _extract_ok(raw):
    return {"project": "MARKETING", "issue_type": "Task", "summary": "Landing page", "description": "d"}


class _FakeCaseStore:
    def __init__(self):
        self.rows = {}

    def transition(self, case_id, new_status, actor_id, detail, *, issue_key=None):
        self.rows[case_id]["status"] = new_status
        if issue_key:
            self.rows[case_id]["issue_key"] = issue_key
        return self.rows[case_id]

    def get_case(self, case_id):
        return self.rows.get(case_id)


def _seed_store(status="draft"):
    store = _FakeCaseStore()
    store.rows["cid"] = {"case_id": "cid", "status": status, "employee_email": "employee@gsvh.test",
                         "approver_email": "cmo@gsvh.test", "issue_key": None}
    return store


def _graph(store, jira=None, saver=None, extract_fn=_extract_ok):
    return jg.build_jira_graph(
        jira=jira or MockJira(projects=["MARKETING"]), checkpointer=saver or InMemorySaver(),
        extract_fn=extract_fn, case_store=store,
        # The graph reloads identity per node; stub the loader so these stay DB-free.
        principal_loader=lambda user_id: _p(),
    )


def test_happy_path_pauses_for_approval():
    store = _seed_store()
    g = _graph(store)
    row = jg.start_case(g, case_id="cid", principal=_p(), raw_text="marketing landing page",
                        approver_email="cmo@gsvh.test")
    assert row["status"] == "pending_approval"


def test_approve_creates_issue():
    store = _seed_store()
    g = _graph(store)
    jg.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="cmo@gsvh.test")
    row = jg.resume_case(g, case_id="cid", decision="approve", actor_id="cmo@gsvh.test")
    assert row["status"] == "created"
    assert row["issue_key"] == "MARKETING-1"


def test_deny_ends_denied_approver():
    store = _seed_store()
    g = _graph(store)
    jg.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="cmo@gsvh.test")
    row = jg.resume_case(g, case_id="cid", decision="deny", actor_id="cmo@gsvh.test")
    assert row["status"] == "denied_approver"


def test_validation_fail_never_reaches_approval():
    store = _seed_store()
    bad_extract = lambda raw: {"project": "SECRET", "issue_type": "Task", "summary": "x", "description": ""}
    g = _graph(store, extract_fn=bad_extract)
    row = jg.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="cmo@gsvh.test")
    assert row["status"] == "denied_validation"


def test_resume_after_rebuild_survives():
    saver = InMemorySaver()
    store = _seed_store()
    g1 = _graph(store, saver=saver)
    jg.start_case(g1, case_id="cid", principal=_p(), raw_text="x", approver_email="cmo@gsvh.test")
    g2 = _graph(store, saver=saver)  # simulate process restart on the same checkpointer
    row = jg.resume_case(g2, case_id="cid", decision="approve", actor_id="cmo@gsvh.test")
    assert row["status"] == "created"
