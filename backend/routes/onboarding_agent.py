"""routes/onboarding_agent.py
--------------------------
The onboarding write endpoints — JWT-native and in-app. Requester == subject: the new
hire files their own Case, so identity is the server-built Principal from the JWT
(`principal_from_user(get_current_user)`), never typed identity. The manager decides.

This route is TRANSPORT ONLY. The filing itself — the ONE extraction, the validator gate
(off-catalog => denied_policy), the no-manager gate (=> unroutable), idempotency, and
seeding the approved role/tools into the graph — lives in services/write_intake.py, the
single implementation shared with the chat write lane. The route authenticates, computes
the idempotency key (client-supplied or a raw-text hash), delegates to `file_onboarding`,
and maps the returned Filing (or its ExtractionFailed) to the response. On decision we
re-check that the caller's JWT identity == the Case's approver_email before resuming
(never trust a case_id in a URL to imply authority).

All routes are absent (404) unless ONBOARDING_AGENT_ENABLED."""
import hashlib

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.config import ONBOARDING_AGENT_ENABLED
from backend.core.onboarding_case import get_case, list_audit
from backend.core.tools.principal import principal_from_user
from backend.routes.deps import traced_user
from backend.services.onboarding_graph import resume_case
from backend.services.write_intake import ExtractionFailed, file_onboarding

router = APIRouter(prefix="/agents/onboarding", tags=["Onboarding Agent"])

# Process-wide compiled graph + HRIS, built at startup (main.py) and injected here as
# module state — the same seam routes/jira_agent.py uses for its graph.
_GRAPH = None
_HRIS = None


def set_graph(graph) -> None:
    global _GRAPH
    _GRAPH = graph


def set_hris(hris) -> None:
    global _HRIS
    _HRIS = hris


def _guard() -> None:
    if not ONBOARDING_AGENT_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


def _idempotency_key(body: dict, email: str, text: str) -> str:
    """Client-supplied intent key (primary), else a content hash over email:RAW TEXT.

    Keyed off the raw text, never the model's output: extraction is probabilistic, so a
    re-clicked submit that extracts even slightly differently would hash to a different
    key and fork a SECOND Case for the same intent. The text is the one deterministic
    thing the user actually gave us."""
    supplied = (body.get("idempotency_key") or "").strip()
    if supplied:
        return supplied
    raw = "|".join([email or "", text])
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("")
async def start_onboarding(request: Request, user: dict = Depends(traced_user)):
    _guard()
    principal = principal_from_user(user)
    body = await request.json()
    text = body["text"]
    idem = _idempotency_key(body, principal.email, text)
    try:
        filing = file_onboarding(principal, text, hris=_HRIS, graph=_GRAPH, key=idem)
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail="Could not understand the request") from exc

    resp = {"case_id": filing.case_id, "status": filing.status,
            "approver_email": filing.approver_email}
    detail = filing.detail or {}
    if "role" in detail:
        resp["role"] = detail["role"]
    if "tools" in detail:
        resp["tools"] = detail["tools"]
    if filing.reason:
        resp["reason"] = filing.reason
    return resp


@router.post("/{case_id}/decision")
async def decide_onboarding(case_id: str, request: Request, user: dict = Depends(traced_user)):
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
    return {"case_id": case_id, "status": row["status"], "grant_id": row.get("grant_id")}


@router.get("/{case_id}")
def read_onboarding_case(case_id: str, user: dict = Depends(traced_user)):
    _guard()
    case = get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    principal = principal_from_user(user)
    # Object-level authz: a case_id in a URL confers no authority by itself.
    if principal.email not in (case["employee_email"], case["approver_email"]):
        raise HTTPException(status_code=403, detail="Not your case")
    return {"case_id": case_id, "status": case["status"], "role": case["role"],
            "tools": case["tools"], "grant_id": case.get("grant_id"),
            "audit": list_audit(case_id)}
