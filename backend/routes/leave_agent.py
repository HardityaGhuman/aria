"""routes/leave_agent.py
----------------------
The Slack leave write endpoints. n8n calls these; n8n makes NO authz/validation/write
decision — it verifies the Slack signature at the edge and forwards. Here we re-verify
the Slack signature, authenticate n8n by shared bearer, resolve a SERVER Principal from
the verified slack_user_id (never trusting typed identity), drive the write Case, and —
on the decision endpoint — re-check that the clicking user is the Case's approver. All
routes are absent (404) unless LEAVE_AGENT_ENABLED."""
import hashlib

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.config import LEAVE_AGENT_ENABLED
from backend.core.hris.mock import MockHRIS
from backend.core.leave_case import (
    create_case,
    get_case,
    get_case_by_idempotency_key,
    transition,
)
from backend.core.slack_identity import principal_for_slack, slack_user_for_email
from backend.core.slack_verify import require_n8n_secret, verify_slack_signature
from backend.routes.deps import open_trace
from backend.services.leave_extract import extract_leave_fields
from backend.services.leave_graph import resume_case, start_case
from backend.services.leave_validator import compute_days

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


def _idempotency_key(email: str, text: str) -> str:
    """The Slack edge sends no client key, so the key is a hash of the one deterministic
    input the user gave: the raw message text."""
    raw = "|".join([email or "", text])
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("")
async def start_leave(request: Request):
    _guard(request)
    await _verify_slack(request)
    payload = await request.json()
    principal = principal_for_slack(payload["slack_user_id"])
    if principal is None:
        raise HTTPException(status_code=401, detail="Slack account not linked. Please link it first.")

    # The key hashes the RAW TEXT, never the model's output. It used to be
    # "{email}:{start_date}:{end_date}" — and those dates come OUT OF THE LLM, so a
    # re-sent Slack message that extracted even one day differently hashed to a different
    # key and forked a second Case for one intent. The duplicate check also runs BEFORE the
    # extract: a Case already moving is READ, not re-driven onto its parked thread.
    idem = _idempotency_key(principal.email, payload["text"])
    dup = get_case_by_idempotency_key(idem)
    if dup is not None and dup["status"] != "draft":
        return {"case_id": str(dup["case_id"]), "status": dup["status"]}

    fields = extract_leave_fields(payload["text"])
    days = compute_days(fields["start_date"], fields["end_date"])
    approver_email = _HRIS.manager_email(principal)
    case = create_case(principal.email, approver_email, fields["start_date"], fields["end_date"],
                       days, fields["reason"], idem)

    if approver_email is None:
        transition(case["case_id"], "unroutable", "system", "no manager linked")
        return {"case_id": str(case["case_id"]), "status": "unroutable"}

    row = start_case(_GRAPH, case_id=str(case["case_id"]), principal=principal,
                     raw_text=payload["text"], approver_email=approver_email)
    approver_slack = slack_user_for_email(approver_email) if row["status"] == "pending_approval" else None
    return {"case_id": str(row["case_id"]), "status": row["status"], "approver_slack_user_id": approver_slack}


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
