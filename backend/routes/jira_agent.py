"""routes/jira_agent.py
---------------------
The Jira work-request write endpoints — JWT-native and in-app (no Slack, no n8n). Both
parties are already authenticated in Aria, so identity is the server-built Principal from
the JWT (`principal_from_user(get_current_user)`), never typed identity.

This route is TRANSPORT ONLY. The filing itself — one extraction, approver resolution,
the unroutable gate, idempotency, starting the Case graph — lives in
services/write_intake.py, the single implementation shared with the chat write lane. Two
copies of "who approves this" and "what key dedupes this" would be two copies of a
security boundary that drift; so the route authenticates, computes the idempotency key,
delegates to `file_jira`, and maps the returned Filing to the response. The requester
starts a Case; the project approver decides. On decision we re-check that the caller's JWT
identity == the Case's `approver_email` before resuming. All routes are absent (404)
unless JIRA_AGENT_ENABLED."""
import hashlib

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.config import JIRA_AGENT_ENABLED
from backend.core.jira_case import get_case
from backend.core.tools.principal import principal_from_user
from backend.routes.deps import traced_user
from backend.services.jira_graph import resume_case
from backend.services.write_intake import file_jira

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


def _idempotency_key(body: dict, email: str, text: str) -> str:
    """Client-supplied intent key (primary), else a hash of the RAW TEXT — never of the
    model's output. Extraction is probabilistic: a re-clicked submit that summarizes even
    slightly differently would hash to a different key and fork a SECOND Case for one
    intent. The raw text is the only deterministic input the user actually gave us."""
    supplied = (body.get("idempotency_key") or "").strip()
    if supplied:
        return supplied
    raw = "|".join([email or "", text])
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("")
async def start_jira(request: Request, user: dict = Depends(traced_user)):
    _guard()
    principal = principal_from_user(user)
    body = await request.json()
    text = body["text"]
    idem = _idempotency_key(body, principal.email, text)
    filing = file_jira(principal, text, graph=_GRAPH, key=idem)
    return {"case_id": filing.case_id, "status": filing.status,
            "approver_email": filing.approver_email}


@router.post("/{case_id}/decision")
async def decide_jira(case_id: str, request: Request, user: dict = Depends(traced_user)):
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
