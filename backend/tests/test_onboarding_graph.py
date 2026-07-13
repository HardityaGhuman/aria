"""Onboarding write graph: pauses for the manager, provisions on approve, denies on
deny, validation short-circuits before the gate, the pause survives a rebuild, and
identity is rebuilt at the write (never replayed from the checkpoint)."""
from langgraph.checkpoint.memory import InMemorySaver

from backend.core.access.mock import MockAccessProvisioner
from backend.core.tools.principal import Principal
from backend.services import onboarding_graph as og


def _p(email="newhire@gsvh.test"):
    return Principal(user_id=1, email=email, role="employee", region="us")


def _extract_ok(raw):
    return {"role": "backend-eng", "extra_tools": ["figma"]}


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


def _seed_store(status="draft"):
    store = _FakeCaseStore()
    store.rows["cid"] = {"case_id": "cid", "status": status, "employee_email": "newhire@gsvh.test",
                         "approver_email": "manager@gsvh.test", "grant_id": None, "attempt": 0}
    return store


def _graph(store, provisioner=None, saver=None, extract_fn=_extract_ok, loader=None):
    return og.build_onboarding_graph(
        provisioner=provisioner or MockAccessProvisioner(),
        checkpointer=saver or InMemorySaver(),
        extract_fn=extract_fn, case_store=store,
        # The graph reloads identity per node; stub the loader so these stay DB-free.
        principal_loader=loader or (lambda user_id: _p()),
    )


def test_happy_path_pauses_for_the_manager():
    store = _seed_store()
    g = _graph(store)
    row = og.start_case(g, case_id="cid", principal=_p(), raw_text="backend engineer, plus figma",
                        approver_email="manager@gsvh.test")
    assert row["status"] == "pending_approval"


def test_approve_provisions_the_resolved_bundle():
    store = _seed_store()
    prov = MockAccessProvisioner()
    g = _graph(store, provisioner=prov)
    og.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="manager@gsvh.test")
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "provisioned"
    assert row["grant_id"]
    # bundle ∪ extras, sorted — the agent KNEW what a backend hire gets.
    assert prov.grants["cid"]["tools"] == ["figma", "github", "jira", "slack", "staging-db"]


def test_deny_ends_denied_manager_and_never_grants():
    store = _seed_store()
    prov = MockAccessProvisioner()
    g = _graph(store, provisioner=prov)
    og.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="manager@gsvh.test")
    row = og.resume_case(g, case_id="cid", decision="deny", actor_id="manager@gsvh.test")
    assert row["status"] == "denied_manager"
    assert prov.calls == 0


def test_validation_fail_never_reaches_approval():
    store = _seed_store()
    prov = MockAccessProvisioner()
    bad = lambda raw: {"role": "designer", "extra_tools": ["prod-root"]}
    g = _graph(store, provisioner=prov, extract_fn=bad)
    row = og.start_case(g, case_id="cid", principal=_p(), raw_text="root pls",
                        approver_email="manager@gsvh.test")
    assert row["status"] == "denied_policy"
    assert prov.calls == 0


def test_resume_after_rebuild_survives():
    saver = InMemorySaver()
    store = _seed_store()
    g1 = _graph(store, saver=saver)
    og.start_case(g1, case_id="cid", principal=_p(), raw_text="x", approver_email="manager@gsvh.test")
    g2 = _graph(store, saver=saver)   # simulate a process restart on the same checkpointer
    row = og.resume_case(g2, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "provisioned"


def test_pre_extracted_fields_skip_the_model_entirely():
    """Ref3 §4: the model is probabilistic. The route already extracted and wrote those
    tools onto the Case row the manager will approve — re-running the model here could
    return a DIFFERENT bundle, so the manager approves one set and the connector grants
    another. Seeded fields => the model is never called."""
    store = _seed_store()
    prov = MockAccessProvisioner()
    calls = {"n": 0}

    def counting_extract(raw):
        calls["n"] += 1
        return {"role": "analyst", "extra_tools": []}

    g = _graph(store, provisioner=prov, extract_fn=counting_extract)
    og.start_case(g, case_id="cid", principal=_p(), raw_text="x",
                  approver_email="manager@gsvh.test",
                  role="designer", extra_tools=["notion"])
    og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert calls["n"] == 0                                   # the model never ran
    assert prov.grants["cid"]["tools"] == ["figma", "notion", "slack"]   # the ROUTE's bundle


def test_offboarded_requester_at_provision_fails_closed():
    """The Task-A invariant, on the third agent: identity is rebuilt AFTER the sleep.
    The user vanished while the Case waited => no grant, ever."""
    store = _seed_store()
    prov = MockAccessProvisioner()
    calls = {"n": 0}

    def vanishing_loader(user_id):
        calls["n"] += 1
        return _p() if calls["n"] == 1 else None   # alive at validate, gone at provision

    g = _graph(store, provisioner=prov, loader=vanishing_loader)
    og.start_case(g, case_id="cid", principal=_p(), raw_text="x", approver_email="manager@gsvh.test")
    row = og.resume_case(g, case_id="cid", decision="approve", actor_id="manager@gsvh.test")
    assert row["status"] == "write_failed"
    assert prov.calls == 0
