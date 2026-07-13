"""Write-path trace: the orchestrator (never the LLM, never the tool) emits one
event per node and per write attempt. Redaction is enforced by an ALLOWLIST in
code, so a future caller cannot leak a new field by accident."""
import json

import pytest

from backend.core.write import trace as wt


@pytest.fixture()
def emitted(monkeypatch):
    records = []
    monkeypatch.setattr(wt, "emit_record", lambda rec: records.append(rec))
    return records


def test_node_events_carry_ids_status_latency(emitted):
    wt.case_node_started("cid", "provision")
    wt.case_node_completed("cid", "provision", "provisioned", 42)
    assert emitted[0]["event"] == "case_node_started"
    assert emitted[0]["case_id"] == "cid"
    assert emitted[0]["node"] == "provision"
    assert emitted[1] == {"event": "case_node_completed", "trace_id": None, "case_id": "cid",
                          "node": "provision", "status": "provisioned", "latency_ms": 42}


def test_write_attempt_and_result_events(emitted):
    wt.case_write_attempted("cid", 2, "access-provisioner")
    wt.case_write_result("cid", 2, "failed", latency_ms=11, failure_class="transient")
    assert emitted[0]["attempt"] == 2
    assert emitted[0]["connector"] == "access-provisioner"
    assert emitted[1]["outcome"] == "failed"
    assert emitted[1]["failure_class"] == "transient"


def test_gate_events(emitted):
    wt.case_interrupted("cid", "request_approval")
    wt.case_resumed("cid", "approve")
    assert emitted[0]["event"] == "case_interrupted"
    assert emitted[1]["decision"] == "approve"


def test_disallowed_fields_are_dropped_not_emitted(emitted):
    # A caller tries to attach PII / payload. The allowlist drops it silently.
    wt.emit_case_event("case_node_completed", case_id="cid", node="provision",
                       status="provisioned", latency_ms=1,
                       employee_email="alice@gsvh.test",
                       tools=["github", "staging-db"],
                       token="eyJhbGciOi.SECRET")
    blob = json.dumps(emitted[0])
    assert "alice@gsvh.test" not in blob
    assert "staging-db" not in blob
    assert "eyJhbGciOi.SECRET" not in blob
    assert set(emitted[0]) <= wt.ALLOWED_FIELDS | {"event", "trace_id"}
