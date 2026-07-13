"""The write boundary under failure. Everything the 2026-07-13 audit said we didn't
have: classify -> bounded idempotent retry -> circuit breaker -> dead letter -> replay.

The load-bearing assertion in almost every test is `len(prov.grants) == 1` — a retry
or replay must NEVER double-grant. That is the single highest-consequence bug in the
slice, and idempotency-by-case_id is the only thing standing between us and it."""
from langgraph.checkpoint.memory import InMemorySaver

from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.principal import Principal
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.errors import PermanentWriteError, TransientWriteError
from backend.services import onboarding_graph as og


def _p():
    return Principal(user_id=1, email="newhire@gsvh.test", role="employee", region="us")


def _extract_ok(raw):
    return {"role": "designer", "extra_tools": []}


class _FakeCaseStore:
    def __init__(self):
        self.rows = {}

    def transition(self, case_id, new_status, actor_id, detail, *, grant_id=None,
                   attempt=None, failure_reason=None):
        row = self.rows[case_id]
        row["status"] = new_status
        if grant_id:
            row["grant_id"] = grant_id
        if attempt is not None:
            row["attempt"] = attempt
        if failure_reason:
            row["failure_reason"] = failure_reason
        return row

    def get_case(self, case_id):
        return self.rows.get(case_id)


def _seed_store():
    store = _FakeCaseStore()
    store.rows["cid"] = {"case_id": "cid", "status": "draft", "employee_email": "newhire@gsvh.test",
                         "approver_email": "manager@gsvh.test", "grant_id": None, "attempt": 0}
    return store


def _graph(store, prov, saver=None, breaker=None):
    return og.build_onboarding_graph(
        provisioner=prov, checkpointer=saver or InMemorySaver(), extract_fn=_extract_ok,
        case_store=store, principal_loader=lambda uid: _p(),
        breaker=breaker or CircuitBreaker("test", threshold=3),
    )


def _to_approval(g):
    og.start_case(g, case_id="cid", principal=_p(), raw_text="designer",
                  approver_email="manager@gsvh.test")


def test_transient_then_success_retries_and_grants_once():
    store, prov = _seed_store(), MockAccessProvisioner(fail_times=1, fail_with=TransientWriteError)
    g = _graph(store, prov)
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "provisioned"
    assert row["attempt"] == 2            # attempt 1 failed transiently, attempt 2 won
    assert prov.calls == 2
    assert len(prov.grants) == 1          # ONE grant, not two


def test_transient_exhausts_budget_and_dead_letters():
    store, prov = _seed_store(), MockAccessProvisioner(fail_times=99, fail_with=TransientWriteError)
    g = _graph(store, prov)
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["attempt"] == 3            # WRITE_MAX_ATTEMPTS
    assert prov.calls == 3                # bounded — not an infinite loop
    assert row["failure_reason"] == "transient"
    assert len(prov.grants) == 0


def test_permanent_error_fails_fast_without_retry():
    store, prov = _seed_store(), MockAccessProvisioner(fail_times=99, fail_with=PermanentWriteError)
    g = _graph(store, prov)
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "write_failed"
    assert prov.calls == 1                # ONE attempt: never retry what will never work
    assert row["attempt"] == 1


def test_open_breaker_short_circuits_the_connector():
    store, prov = _seed_store(), MockAccessProvisioner()
    breaker = CircuitBreaker("test", threshold=1)
    breaker.record_failure()              # already open before this Case reaches the write
    g = _graph(store, prov, breaker=breaker)
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "dead_letter"
    assert prov.calls == 0                # the connector was NEVER called
    assert row["failure_reason"] == "breaker_open"


def test_breaker_opens_after_consecutive_transient_failures():
    store, prov = _seed_store(), MockAccessProvisioner(fail_times=99, fail_with=TransientWriteError)
    breaker = CircuitBreaker("test", threshold=3)
    g = _graph(store, prov, breaker=breaker)
    _to_approval(g)
    og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert breaker.is_open() is True      # 3 consecutive failures in one Case


def test_successful_write_resets_the_breaker():
    store, prov = _seed_store(), MockAccessProvisioner(fail_times=1, fail_with=TransientWriteError)
    breaker = CircuitBreaker("test", threshold=3)
    g = _graph(store, prov, breaker=breaker)
    _to_approval(g)
    og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert breaker.consecutive_failures == 0


def test_partial_grant_is_a_failure_not_a_success():
    """Ref2 §3, the canonical silent failure: the connector answers cleanly but the
    payload does not match what was approved. Execution succeeded; the WRITE did not.
    Marking this `provisioned` would write a lie into the audit log."""
    class _LyingProvisioner:
        calls = 0
        grants: dict = {}

        def grant(self, principal, case_id, tools):
            _LyingProvisioner.calls += 1
            return {"grant_id": "grant-x", "tools": []}      # 200 OK, output: []

    store = _seed_store()
    g = _graph(store, _LyingProvisioner())
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "write_failed"        # permanent: it will lie again
    assert row["failure_reason"] == "permanent"


def test_replay_of_a_dead_letter_provisions_once():
    """The DLQ is a queue, not a graveyard: the connector recovers, an admin replays,
    the SAME thread resumes at provision — no re-extraction, no second approval, and
    exactly ONE grant even though the earlier attempts already ran."""
    saver = InMemorySaver()
    store = _seed_store()
    prov = MockAccessProvisioner(fail_times=99, fail_with=TransientWriteError)
    g = _graph(store, prov, saver=saver)
    _to_approval(g)
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "dead_letter"

    prov._fail_remaining = 0              # the connector recovers
    row = og.replay_case(g, case_id="cid", actor_id="hr@gsvh.test")
    assert row["status"] == "provisioned"
    assert row["grant_id"]
    assert len(prov.grants) == 1          # ONE grant total, across 3 failures + a replay
