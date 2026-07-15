"""Leave at the write boundary. Before this, ANY error at the HRIS — a timeout, a 503 —
terminated an approved leave request at write_failed, forever. These are the behaviours it
never had."""
from datetime import date

from langgraph.checkpoint.memory import InMemorySaver

from backend.core.hris.mock import MockHRIS
from backend.core.tools.principal import Principal
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.errors import PermanentWriteError, TransientWriteError
from backend.services import leave_graph as lg

PRINCIPAL = Principal(user_id=1, email="employee@gsvh.test", role="employee", region="us")


class _Store:                      # module-shaped fake, mirrors backend.core.leave_case
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


def _graph(hris, store, breaker=None):
    return lg.build_leave_graph(
        hris=hris, checkpointer=InMemorySaver(), case_store=store,
        extract_fn=lambda t: {"start_date": "2026-08-12", "end_date": "2026-08-13",
                              "reason": "trip"},
        today=date(2026, 8, 1), principal_loader=lambda uid: PRINCIPAL,
        breaker=breaker or CircuitBreaker("hris", threshold=3),
    )


def _run_to_approval(graph, store, cid="c1"):
    store.create(cid)
    lg.start_case(graph, case_id=cid, principal=PRINCIPAL, raw_text="2 days",
                  approver_email="m@gsvh.test")


def test_a_transient_failure_is_retried_and_then_succeeds():
    store, hris = _Store(), MockHRIS(fail_times=1, fail_with=TransientWriteError)
    graph = _graph(hris, store)
    _run_to_approval(graph, store)
    row = lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "booked"
    assert row["attempt"] == 2                       # attempt 1 failed, attempt 2 booked


def test_transient_failures_past_the_budget_dead_letter_and_stay_replayable():
    store, hris = _Store(), MockHRIS(fail_times=99, fail_with=TransientWriteError)
    graph = _graph(hris, store)
    _run_to_approval(graph, store)
    row = lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["failure_reason"] == "transient"
    assert row["attempt"] == 3                       # WRITE_MAX_ATTEMPTS


def test_a_permanent_failure_never_retries():
    store, hris = _Store(), MockHRIS(fail_times=99, fail_with=PermanentWriteError)
    graph = _graph(hris, store)
    _run_to_approval(graph, store)
    row = lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "write_failed"
    assert row["attempt"] == 1                       # one attempt, then stop


def test_open_breaker_dead_letters_without_touching_the_hris():
    store, hris = _Store(), MockHRIS()
    breaker = CircuitBreaker("hris", threshold=1)
    breaker.record_failure()                          # open
    graph = _graph(hris, store, breaker=breaker)
    _run_to_approval(graph, store)
    row = lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["failure_reason"] == "breaker_open"
    assert hris._bookings == {}                       # the connector was never called


def test_replay_of_a_dead_lettered_case_books_exactly_once():
    store = _Store()
    hris = MockHRIS(fail_times=99, fail_with=TransientWriteError)
    graph = _graph(hris, store)
    _run_to_approval(graph, store)
    lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert store.rows["c1"]["status"] == "dead_letter"

    hris._fail_remaining = 0                          # the connector recovers
    row = lg.replay_case(graph, case_id="c1", actor_id="hr@gsvh.test")
    assert row["status"] == "booked"
    assert row["confirmation_id"]
    assert store.audit.count("booked") == 1           # no double booking


def test_a_booking_that_comes_back_with_the_wrong_dates_is_permanent():
    """Execution is not correctness: the HRIS answered, but not with the leave the manager
    approved. Booking it would write a lie into the audit log."""
    class _WrongHRIS(MockHRIS):
        def submit_leave(self, principal, case_id, start_date, end_date, days):
            booked = super().submit_leave(principal, case_id, start_date, end_date, days)
            return {**booked, "start_date": "2027-01-01"}    # not what was approved

    store = _Store()
    graph = _graph(_WrongHRIS(), store)
    _run_to_approval(graph, store)
    row = lg.resume_case(graph, case_id="c1", decision="approve", actor_id="m@gsvh.test")
    assert row["status"] == "write_failed"
