"""routes/leave_agent.py
----------------------
The Slack leave write endpoints. n8n calls these; n8n makes NO authz/validation/write
decision — it verifies the Slack signature at the edge and forwards. Here we re-verify
the Slack signature, authenticate n8n by shared bearer, resolve a SERVER Principal from
the verified slack_user_id (never trusting typed identity), delegate the filing, and —
on the decision endpoint — re-check that the clicking user is the Case's approver.

This route is TRANSPORT ONLY. The filing itself — raw-text idempotency, approver
resolution from the HRIS, the no-manager gate, starting the Case graph — lives in
services/write_intake.py, the single implementation shared with the chat write lane and
the other agents. The only leave-specific transport concern left here is translating the
approver's EMAIL back to a Slack user id for the button payload n8n posts. All routes are
absent (404) unless LEAVE_AGENT_ENABLED."""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.config import LEAVE_AGENT_ENABLED
from backend.core.hris.mock import MockHRIS
from backend.core.leave_case import get_case
from backend.core.slack_identity import principal_for_slack, slack_user_for_email
from backend.core.slack_verify import require_n8n_secret, verify_slack_signature
from backend.routes.deps import open_trace
from backend.services.leave_graph import resume_case
from backend.services.write_intake import file_leave

# Router-level trace: this edge has no JWT (the caller is n8n, the human is a Slack id),
# so there is no user to hang the trace on — but the Case's graph events and its extract
# LLM span still need one id to be joined by.
router = APIRouter(prefix="/agents/leave", tags=["Leave Agent"],
                   dependencies=[Depends(open_trace)])

# Process-wide compiled graph over the Postgres checkpointer — built at startup
# (see main.py wiring, Task 12) and injected here as module state.
_GRAPH = None
_HRIS = MockHRIS()


def set_graph(graph) -> None:
    global _GRAPH
    _GRAPH = graph


def _guard(request: Request) -> None:
    if not LEAVE_AGENT_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    if not require_n8n_secret(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Unauthorized caller")


async def _verify_slack(request: Request) -> bytes:
    body = await request.body()
    ok = verify_slack_signature(
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Bad Slack signature")
    return body


@router.post("")
async def start_leave(request: Request):
    _guard(request)
    await _verify_slack(request)
    payload = await request.json()
    principal = principal_for_slack(payload["slack_user_id"])
    if principal is None:
        raise HTTPException(status_code=401, detail="Slack account not linked. Please link it first.")

    # file_leave keys off the RAW TEXT (never the model's dates), reads a duplicate rather
    # than re-driving a parked graph, resolves the approver from the HRIS, and parks at the
    # gate — the same filing the chat lane runs.
    filing = file_leave(principal, payload["text"], hris=_HRIS, graph=_GRAPH)
    approver_slack = (slack_user_for_email(filing.approver_email)
                      if filing.status == "pending_approval" else None)
    return {"case_id": filing.case_id, "status": filing.status,
            "approver_slack_user_id": approver_slack}


@router.post("/{case_id}/decision")
async def decide_leave(case_id: str, request: Request):
    _guard(request)
    await _verify_slack(request)
    payload = await request.json()
    case = get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    clicker = principal_for_slack(payload["slack_user_id"])
    if clicker is None or clicker.email != case["approver_email"]:
        raise HTTPException(status_code=403, detail="Not the approver for this case")
    decision = "approve" if payload["decision"] == "approve" else "deny"
    row = resume_case(_GRAPH, case_id=case_id, decision=decision, actor_id=clicker.email)
    return {"case_id": case_id, "status": row["status"], "confirmation_id": row.get("confirmation_id")}
