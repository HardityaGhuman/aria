"""Jira at the write boundary. Same four behaviours Leave just gained, plus the
agent-specific verification: an issue key whose project is not the approved project means
the connector filed the work in the wrong place — a permanent failure, never a success."""
from langgraph.checkpoint.memory import InMemorySaver

from backend.core.jira.mock import MockJira
from backend.core.tools.principal import Principal
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.errors import PermanentWriteError, TransientWriteError
from backend.services import jira_graph as jg

PRINCIPAL = Principal(user_id=1, email="employee@gsvh.test", role="employee", region="us")
FIELDS = {"project": "MARKETING", "issue_type": "Task", "summary": "s", "description": "d"}


class _Store:                      # module-shaped fake, mirrors backend.core.jira_case
    def __init__(self):
        self.rows, self.audit = {}, []

    def create(self, cid):
        self.rows[cid] = {"case_id": cid, "status": "draft", "attempt": 0}

    def get_case(self, cid):
        return self.rows.get(cid)

    def transition(self, cid, status, actor, detail, **kw):
        self.rows[cid].update(status=status, **{k: v for k, v in kw.items() if v is not None})
        self.audit.append(status)
        return self.rows[cid]


def _graph(jira, store, breaker=None):
    return jg.build_jira_graph(
        jira=jira, checkpointer=InMemorySaver(), case_store=store,
        extract_fn=lambda t: dict(FIELDS), principal_loader=lambda uid: PRINCIPAL,
        breaker=breaker or CircuitBreaker("jira", threshold=3),
    )


def _run_to_approval(graph, store, cid="c1"):
    store.create(cid)
    jg.start_case(graph, case_id=cid, principal=PRINCIPAL, raw_text="do a thing",
                  approver_email="m@gsvh.test")


def test_a_transient_failure_is_retried_and_then_creates():
    store, jira = _Store(), MockJira(fail_times=1, fail_with=TransientWriteError)
    graph = _graph(jira, store)
    _run_to_approval(graph, store)
    row = jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "created"
    assert row["attempt"] == 2                       # attempt 1 failed, attempt 2 created


def test_transient_failures_past_the_budget_dead_letter():
    store, jira = _Store(), MockJira(fail_times=99, fail_with=TransientWriteError)
    graph = _graph(jira, store)
    _run_to_approval(graph, store)
    row = jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["failure_reason"] == "transient"
    assert row["attempt"] == 3                       # WRITE_MAX_ATTEMPTS


def test_a_permanent_failure_never_retries():
    store, jira = _Store(), MockJira(fail_times=99, fail_with=PermanentWriteError)
    graph = _graph(jira, store)
    _run_to_approval(graph, store)
    row = jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "write_failed"
    assert row["attempt"] == 1                       # one attempt, then stop


def test_open_breaker_dead_letters_without_touching_jira():
    store, jira = _Store(), MockJira()
    breaker = CircuitBreaker("jira", threshold=1)
    breaker.record_failure()                          # open
    graph = _graph(jira, store, breaker=breaker)
    _run_to_approval(graph, store)
    row = jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["failure_reason"] == "breaker_open"
    assert jira._issues == {}                         # the connector was never called


def test_replay_of_a_dead_lettered_case_creates_exactly_one_issue():
    store, jira = _Store(), MockJira(fail_times=99, fail_with=TransientWriteError)
    graph = _graph(jira, store)
    _run_to_approval(graph, store)
    jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert store.rows["c1"]["status"] == "dead_letter"

    jira._fail_remaining = 0                          # the connector recovers
    row = jg.replay_case(graph, case_id="c1", actor_id="hr@gsvh.test")
    assert row["status"] == "created"
    assert row["issue_key"]
    assert store.audit.count("created") == 1          # no double file


def test_an_issue_filed_in_the_wrong_project_is_permanent():
    """Execution is not correctness: Jira answered, but filed the work against a project the
    approver never signed off on."""
    class _WrongJira(MockJira):
        def create_issue(self, principal, case_id, project, issue_type, summary, description):
            created = super().create_issue(principal, case_id, project, issue_type,
                                           summary, description)
            return {**created, "issue_key": "OTHER-1"}     # not the approved project

    store = _Store()
    graph = _graph(_WrongJira(), store)
    _run_to_approval(graph, store)
    row = jg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "write_failed"
