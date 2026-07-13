"""routes/jira_agent.py
---------------------
The Jira work-request write endpoints — JWT-native and in-app (no Slack, no n8n). Both
parties are already authenticated in Aria, so identity is the server-built Principal from
the JWT (`principal_from_user(get_current_user)`), never typed identity. The requester
starts a Case; the project approver decides. On decision we re-check that the caller's
JWT identity == the Case's `approver_email` before resuming. All routes are absent (404)
unless JIRA_AGENT_ENABLED."""
import hashlib

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.auth import get_current_user
from backend.core.config import JIRA_AGENT_ENABLED, JIRA_ALLOWED_PROJECTS, JIRA_PROJECT_APPROVERS
from backend.core.jira_case import create_case, get_case, transition
from backend.core.tools.principal import principal_from_user
from backend.services.jira_extract import extract_jira_fields
from backend.services.jira_graph import resume_case, start_case

router = APIRouter(prefix="/agents/jira", tags=["Jira Agent"])

# Process-wide compiled graph over the Postgres checkpointer — built at startup
# (see main.py wiring) and injected here as module state.
_GRAPH = None


def set_graph(graph) -> None:
    global _GRAPH
    _GRAPH = graph


def _guard() -> None:
    if not JIRA_AGENT_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


def _idempotency_key(body: dict, email: str, fields: dict) -> str:
    """Client-supplied intent key (primary), else content-hash fallback over ALL
    content fields (defense-in-depth — the frontend SHOULD always send one)."""
    supplied = (body.get("idempotency_key") or "").strip()
    if supplied:
        return supplied
    raw = "|".join([email, fields["project"], fields["issue_type"],
                    fields["summary"], fields["description"]])
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("")
async def start_jira(request: Request, user: dict = Depends(get_current_user)):
    _guard()
    principal = principal_from_user(user)
    body = await request.json()
    text = body["text"]

    fields = extract_jira_fields(text)
    project = fields["project"]
    approver_email = JIRA_PROJECT_APPROVERS.get(project)
    idem = _idempotency_key(body, principal.email, fields)
    case = create_case(principal.email, approver_email, project, fields["issue_type"],
                       fields["summary"], fields["description"], idem)
    case_id = str(case["case_id"])

    # Undetermined project, or a known project with no approver mapped -> unroutable
    # before the gate. A project NOT in the allowlist falls through to the graph, whose
    # validator ends it at denied_validation (no silent cross-team routing either way).
    if not project or (project in JIRA_ALLOWED_PROJECTS and approver_email is None):
        row = transition(case_id, "unroutable", "system",
                         "no project determined" if not project else "no approver mapped")
        return {"case_id": case_id, "status": row["status"], "approver_email": None}

    row = start_case(_GRAPH, case_id=case_id, principal=principal,
                     raw_text=text, approver_email=approver_email)
    return {"case_id": case_id, "status": row["status"],
            "approver_email": approver_email if row["status"] == "pending_approval" else None}


@router.post("/{case_id}/decision")
async def decide_jira(case_id: str, request: Request, user: dict = Depends(get_current_user)):
    _guard()
    case = get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    principal = principal_from_user(user)
    if principal.email != case["approver_email"]:
        raise HTTPException(status_code=403, detail="Not the approver for this case")
    body = await request.json()
    decision = "approve" if body["decision"] == "approve" else "deny"
    row = resume_case(_GRAPH, case_id=case_id, decision=decision, actor_id=principal.email)
    return {"case_id": case_id, "status": row["status"], "issue_key": row.get("issue_key")}
