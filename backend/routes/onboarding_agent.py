"""routes/onboarding_agent.py
--------------------------
The onboarding write endpoints — JWT-native and in-app. Requester == subject: the new
hire files their own Case, so identity is the server-built Principal from the JWT
(`principal_from_user(get_current_user)`), never typed identity. The manager decides.

Two gates before the graph even starts, both deliberate:
  - no manager in HRIS  => `unroutable`. There is no ungated write, ever, so a Case
                           nobody can approve must END, not proceed.
  - invalid role/tool   => `denied_policy`. The deterministic validator runs here too,
                           so an off-catalog request never costs the manager attention.
On decision we re-check that the caller's JWT identity == the Case's approver_email
before resuming (never trust a case_id in a URL to imply authority).

All routes are absent (404) unless ONBOARDING_AGENT_ENABLED."""
import hashlib

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.core.config import ONBOARDING_AGENT_ENABLED
from backend.core.onboarding_case import (
    create_case,
    get_case,
    get_case_by_idempotency_key,
    list_audit,
    list_dead_letter,
    transition,
)
from backend.core.tools.principal import principal_from_user
from backend.core.write.breaker import get_breaker, reset_breaker
from backend.routes.deps import traced_role, traced_user
from backend.services.onboarding_extract import OnboardingExtractError, extract_onboarding_fields
from backend.services.onboarding_graph import CONNECTOR, replay_case, resume_case, start_case
from backend.services.onboarding_validator import validate_onboarding

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


def _existing(case: dict) -> dict:
    """Answer a duplicate submit from the row that already exists."""
    return {"case_id": str(case["case_id"]), "status": case["status"],
            "role": case.get("role"), "tools": case.get("tools"),
            "approver_email": case.get("approver_email")}


@router.post("")
async def start_onboarding(request: Request, user: dict = Depends(traced_user)):
    _guard()
    principal = principal_from_user(user)
    body = await request.json()
    text = body["text"]
    idem = _idempotency_key(body, principal.email, text)

    # Duplicate submit: the Case already exists and is already moving. READ it, never
    # re-drive it — re-invoking the graph on a thread parked at the approval gate re-runs
    # nodes and appends checkpoints (Ref1 §4's "fake resume"). Also saves the LLM call.
    dup = get_case_by_idempotency_key(idem)
    if dup is not None and dup["status"] != "draft":
        return _existing(dup)

    # The ONE extraction for this Case. Its output becomes the Case row and the graph's
    # seeded state — so the tools the manager approves are exactly the tools the connector
    # grants. A model that returns nothing usable ends the request HERE, before a Case
    # exists: no half-built Case, no unhandled exception.
    try:
        fields = extract_onboarding_fields(text)
    except OnboardingExtractError as exc:
        raise HTTPException(status_code=422, detail="Could not understand the request") from exc

    verdict = validate_onboarding(fields["role"], fields["extra_tools"])
    approver_email = _HRIS.manager_email(principal) if _HRIS else None

    case = create_case(principal.email, approver_email, fields["role"], verdict.tools, idem)
    case_id = str(case["case_id"])

    # Two submits can race past the lookup above; the UNIQUE key then makes create_case
    # return the row the winner already advanced. Second line of defence, same rule.
    if case["status"] != "draft":
        return _existing(case)

    if not verdict.ok:
        row = transition(case_id, "denied_policy", "system", verdict.reason or "validation failed")
        return {"case_id": case_id, "status": row["status"], "reason": verdict.reason,
                "approver_email": None}
    if not approver_email:
        row = transition(case_id, "unroutable", "system", "no manager mapped")
        return {"case_id": case_id, "status": row["status"], "approver_email": None}

    # Seed role/extra_tools so the graph's extract node passes through — one extraction
    # per Case, and the row the manager approves cannot diverge from the graph's state.
    row = start_case(_GRAPH, case_id=case_id, principal=principal,
                     raw_text=text, approver_email=approver_email,
                     role=fields["role"], extra_tools=fields["extra_tools"])
    return {"case_id": case_id, "status": row["status"], "role": fields["role"],
            "tools": verdict.tools,
            "approver_email": approver_email if row["status"] == "pending_approval" else None}


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


# --- admin surface: the DLQ and the breaker ----------------------------------------
# Both exist because automation must be able to HALT and a human must be able to RESUME
# it. Neither is self-healing by design: a breaker that resets itself hides the outage,
# and a Case that replays itself hides the failure that dead-lettered it.
admin_router = APIRouter(prefix="/admin/onboarding", tags=["Onboarding Agent (admin)"])


@admin_router.get("/dead-letter")
def list_dead_letter_cases(_: dict = Depends(traced_role("hr"))):
    """The DLQ. A query (WHERE status = 'dead_letter'), not a second table."""
    _guard()
    return {"cases": list_dead_letter()}


@admin_router.post("/cases/{case_id}/replay")
def replay_onboarding_case(case_id: str, user: dict = Depends(traced_role("hr"))):
    """Resume a dead-lettered Case from its checkpoint. No re-extraction, no second
    approval, no forked audit log — and idempotency by case_id means a replay that
    races a late success cannot double-grant."""
    _guard()
    case = get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="No such case")
    if case["status"] != "dead_letter":
        raise HTTPException(status_code=409, detail=f"Case is {case['status']}, not dead_letter")
    row = replay_case(_GRAPH, case_id=case_id, actor_id=user.get("email") or "admin")
    return {"case_id": case_id, "status": row["status"], "grant_id": row.get("grant_id")}


@admin_router.post("/breaker/reset")
def reset_access_breaker(_: dict = Depends(traced_role("hr"))):
    """Explicit clearance for the access-provisioner breaker. Never time-based: a
    self-healing breaker silently re-enters the outage it exists to surface."""
    _guard()
    reset_breaker(CONNECTOR)
    return {"connector": CONNECTOR, "open": get_breaker(CONNECTOR).is_open()}
