"""End-to-end on the REAL Postgres checkpointer + the REAL case store. The unit tests
run on InMemorySaver, which cannot catch a serialization or thread-namespace bug; this
can. Two proofs:
  1. a Case paused at the gate resumes on a REBUILT graph (i.e. after a process restart)
     and provisions.
  2. a DEAD-LETTERED Case replays on a rebuilt graph and grants exactly ONCE."""
import uuid

import pytest

from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.principal import Principal
from backend.core.write.breaker import CircuitBreaker
from backend.core.write.errors import TransientWriteError
from backend.tests.conftest_pg import requires_pg

pytestmark = requires_pg


def _p():
    return Principal(user_id=1, email="newhire@gsvh.test", role="employee", region="us")


def _extract(raw):
    return {"role": "designer", "extra_tools": []}


@pytest.fixture()
def store():
    from backend.core import onboarding_case
    onboarding_case.initialize_onboarding_case_tables()
    return onboarding_case


def _real_graph(store, prov, breaker=None):
    from backend.services.leave_checkpointer import get_checkpointer
    from backend.services.onboarding_graph import build_onboarding_graph
    return build_onboarding_graph(
        provisioner=prov, checkpointer=get_checkpointer(), extract_fn=_extract,
        case_store=store, principal_loader=lambda uid: _p(),
        breaker=breaker or CircuitBreaker("smoke", threshold=3),
    )


def _draft(store):
    return str(store.create_case("newhire@gsvh.test", "manager@gsvh.test", "designer",
                                 ["figma", "slack"], "onb-smoke-" + uuid.uuid4().hex[:12])["case_id"])


def test_pause_then_resume_on_a_rebuilt_graph_provisions(store):
    from backend.services import onboarding_graph as og
    prov = MockAccessProvisioner()
    case_id = _draft(store)

    g1 = _real_graph(store, prov)
    row = og.start_case(g1, case_id=case_id, principal=_p(), raw_text="designer",
                        approver_email="manager@gsvh.test")
    assert row["status"] == "pending_approval"

    g2 = _real_graph(store, prov)          # process restart: a brand-new graph object
    row = og.resume_case(g2, case_id=case_id, decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "provisioned"
    assert row["grant_id"]
    assert len(prov.grants) == 1


def test_dead_letter_then_replay_on_a_rebuilt_graph_grants_once(store):
    from backend.services import onboarding_graph as og
    prov = MockAccessProvisioner(fail_times=99, fail_with=TransientWriteError)
    case_id = _draft(store)

    g1 = _real_graph(store, prov)
    og.start_case(g1, case_id=case_id, principal=_p(), raw_text="designer",
                  approver_email="manager@gsvh.test")
    row = og.resume_case(g1, case_id=case_id, decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "dead_letter"
    assert row["attempt"] == 3

    prov._fail_remaining = 0               # the connector recovers
    g2 = _real_graph(store, prov)          # process restart before the admin replays
    row = og.replay_case(g2, case_id=case_id, actor_id="hr@gsvh.test")
    assert row["status"] == "provisioned"
    assert len(prov.grants) == 1           # ONE grant, across 3 failures + a restart + a replay
