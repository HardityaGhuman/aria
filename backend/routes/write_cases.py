"""routes/write_cases.py
---------------------
The agent-agnostic Case surface. Until now a Case could only be read if you already knew
its UUID, and nothing anywhere listed Cases at all — so a manager had no way to discover
that a Case was waiting on their approval. The HITL gate is the invariant this whole phase
rests on; a gate nobody can find is a gate nobody can operate.

Authorization is the rule the per-agent routes already enforce: you must be a party to the
Case (its requester or its approver). For the list endpoints the email predicate IS the
boundary — a stranger's list is empty, because there is nothing of theirs to show.

Every route resolves the agent through the registry (`services/write_agents.py`), so this
file never imports a graph and never branches on an agent name."""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.core.tools.principal import principal_from_user
from backend.core.write import case_store
from backend.routes.deps import traced_role, traced_user
from backend.services import write_agents

router = APIRouter(prefix="/agents/cases", tags=["Write Agents"])
admin_router = APIRouter(prefix="/admin/write", tags=["Write Agents (admin)"])


def _agent_or_404(name: str):
    agent = write_agents.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail="No such write agent")
    return agent


@router.get("")
def list_my_cases(role: str = Query("requester", pattern="^(requester|approver)$"),
                  user: dict = Depends(traced_user)):
    """`requester` = the Cases I filed. `approver` = the Cases awaiting MY decision."""
    principal = principal_from_user(user)
    cases = case_store.list_for_user(write_agents.specs(), principal.email, role)
    return {"role": role, "cases": cases}


@router.get("/{agent}/{case_id}")
def read_case(agent: str, case_id: str, user: dict = Depends(traced_user)):
    spec = _agent_or_404(agent).spec
    case = case_store.get_case(spec, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    principal = principal_from_user(user)
    if principal.email not in (case["employee_email"], case["approver_email"]):
        raise HTTPException(status_code=403, detail="Not your case")
    return {"agent": agent, "case": case, "audit": case_store.list_audit(spec, case_id)}


@router.post("/{agent}/{case_id}/decision")
async def decide_case(agent: str, case_id: str, request: Request,
                      user: dict = Depends(traced_user)):
    """THE human gate, one door for every agent. Leave's own decision endpoint demands a
    Slack signature, so before this a manager could only approve a leave Case from Slack —
    the chat could show them the Case but not let them act on it.

    Authority comes from the CASE ROW, re-checked here: the caller must be the approver the
    Case was routed to. Not the requester (nobody approves their own write), not anyone who
    happens to hold the case_id, and never the client's own say-so. A Case that is not
    awaiting a decision is a 409 — a second click must not re-drive a graph that already
    ran past its interrupt."""
    write_agent = _agent_or_404(agent)
    body = await request.json()
    decision = (body or {}).get("decision")
    if decision not in ("approve", "deny"):
        raise HTTPException(status_code=422, detail="decision must be 'approve' or 'deny'")

    case = case_store.get_case(write_agent.spec, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    principal = principal_from_user(user)
    if principal.email != case.get("approver_email"):
        raise HTTPException(status_code=403, detail="Not the approver for this case")
    if case["status"] != "pending_approval":
        raise HTTPException(status_code=409,
                            detail=f"Case is {case['status']}, not pending_approval")

    row = write_agent.resume(write_agent.graph, case_id=case_id, decision=decision,
                             actor_id=principal.email)
    return {"agent": agent, "case_id": case_id, "status": row["status"],
            "result_id": row.get(write_agent.spec.result_column)}


# --- admin surface: the DLQ and the breakers -------------------------------------------
# Automation must be able to HALT, and a human must be able to RESUME it. Neither is
# self-healing by design: a breaker that resets itself hides the outage it exists to
# surface, and a Case that replays itself hides the failure that dead-lettered it.


@admin_router.get("/dead-letter")
def list_dead_letter_cases(_: dict = Depends(traced_role("hr"))):
    """Every stuck Case, every agent. The DLQ is a query, not a table."""
    return {"cases": case_store.list_dead_letter(write_agents.specs())}


@admin_router.post("/cases/{agent}/{case_id}/replay")
def replay_case(agent: str, case_id: str, user: dict = Depends(traced_role("hr"))):
    """Resume a dead-lettered Case from its checkpoint: no re-extraction, no second
    approval, no forked audit log. Idempotency by case_id means a replay that races a late
    success cannot double-write. The replay also clears the connector's breaker — the human
    IS the explicit clearance the breaker demands, and without it a Case that burned its
    transient budget (which is N consecutive failures, i.e. an open breaker) could never be
    drained from the queue."""
    write_agent = _agent_or_404(agent)
    case = case_store.get_case(write_agent.spec, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    if case["status"] != "dead_letter":
        raise HTTPException(status_code=409, detail=f"Case is {case['status']}, not dead_letter")
    row = write_agent.replay(write_agent.graph, case_id=case_id,
                             actor_id=user.get("email") or "admin")
    return {"agent": agent, "case_id": case_id, "status": row["status"],
            "result_id": row.get(write_agent.spec.result_column)}


@admin_router.get("/breakers")
def list_breakers(_: dict = Depends(traced_role("hr"))):
    """The board that answers "is automation currently halted, and against what?"."""
    from backend.core.write.breaker import all_breakers

    return {"breakers": [{"connector": name, "open": b.is_open(),
                          "consecutive_failures": b.consecutive_failures}
                         for name, b in all_breakers().items()]}


@admin_router.post("/breaker/{connector}/reset")
def reset_connector_breaker(connector: str, _: dict = Depends(traced_role("hr"))):
    from backend.core.write.breaker import get_breaker, reset_breaker

    reset_breaker(connector)
    return {"connector": connector, "open": get_breaker(connector).is_open()}
