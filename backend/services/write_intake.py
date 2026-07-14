"""services/write_intake.py
------------------------
THE one way a Case gets filed. Before this, filing lived inside the three transport
routes (Slack for leave, JWT for jira/onboarding); the chat is now the product surface,
so the same filing has to run from the chat pipeline as well. Two copies of a step that
decides the approver and the idempotency key would be two copies of a security boundary,
and they would drift. So: this module files, and the routes delegate to it.

What is shared by every agent (and enforced here, once):
  * the idempotency key hashes the RAW TEXT, never the model's output — extraction is
    probabilistic, so keying off it forks a second Case for one intent;
  * a Case that already exists and is already moving is READ, never re-driven — resuming
    a graph parked at its approval interrupt re-runs nodes and appends checkpoints;
  * the approver is resolved from a SYSTEM of record (the HRIS, or the project map),
    never from the message; the user does not get to name their own approver;
  * nothing reaches a human gate that a deterministic validator already refused.

What is agent-specific (and lives in one function each): which fields are extracted, who
the approver is, and what the request looks like when a human reads it (`summary`).
"""
import hashlib
from dataclasses import dataclass, field

from backend.core import jira_case, leave_case, onboarding_case
from backend.core.config import JIRA_ALLOWED_PROJECTS, JIRA_PROJECT_APPROVERS
from backend.core.tools.principal import Principal
from backend.services.jira_extract import extract_jira_fields
from backend.services.jira_graph import start_case as jira_start_case
from backend.services.jira_validator import validate_jira
from backend.services.leave_extract import extract_leave_fields
from backend.services.leave_graph import start_case as leave_start_case
from backend.services.leave_validator import compute_days
from backend.services.onboarding_extract import OnboardingExtractError, extract_onboarding_fields
from backend.services.onboarding_graph import start_case as onboarding_start_case
from backend.services.onboarding_validator import validate_onboarding


class UnknownAgentError(Exception):
    """A write agent that is not registered (or whose kill switch is off)."""


class ExtractionFailed(Exception):
    """The model could not turn the message into fields. The Case is never created:
    a half-built Case is worse than no Case."""


@dataclass
class Filing:
    """What the caller (an HTTP route, or the chat) shows a human."""
    agent: str
    case_id: str
    status: str
    approver_email: str | None = None
    summary: str = ""                       # the one-liner a human reads on the card
    detail: dict = field(default_factory=dict)
    reason: str | None = None               # why it was denied, when it was


def idempotency_key(email: str, text: str) -> str:
    raw = "|".join([email or "", text])
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _existing(agent: str, case: dict, summary: str = "") -> Filing:
    return Filing(agent=agent, case_id=str(case["case_id"]), status=case["status"],
                  approver_email=case.get("approver_email"), summary=summary,
                  detail=dict(case))


# --- leave ----------------------------------------------------------------------------

def file_leave(principal: Principal, text: str, *, store=leave_case, hris, graph,
               extract_fn=None, key: str | None = None) -> Filing:
    extract_fn = extract_fn or extract_leave_fields
    idem = key or idempotency_key(principal.email, text)
    dup = store.get_case_by_idempotency_key(idem)
    if dup is not None and dup["status"] != "draft":
        return _existing("leave", dup)

    fields = extract_fn(text)
    days = compute_days(fields["start_date"], fields["end_date"])
    approver_email = hris.manager_email(principal)
    summary = f"{fields['start_date']} to {fields['end_date']} · {days} day(s)"

    case = store.create_case(principal.email, approver_email, fields["start_date"],
                             fields["end_date"], days, fields["reason"], idem)
    case_id = str(case["case_id"])
    if case["status"] != "draft":          # a racing duplicate won the unique key
        return _existing("leave", case, summary)

    detail = {"start_date": fields["start_date"], "end_date": fields["end_date"],
              "days": days, "reason": fields["reason"]}
    if not approver_email:
        row = store.transition(case_id, "unroutable", "system", "no manager linked")
        return Filing("leave", case_id, row["status"], None, summary, detail,
                      "No manager is mapped to you, so there is nobody to approve this.")

    row = leave_start_case(graph, case_id=case_id, principal=principal, raw_text=text,
                           approver_email=approver_email)
    return Filing("leave", case_id, row["status"], approver_email, summary, detail,
                  row.get("detail"))


# --- jira -----------------------------------------------------------------------------

def file_jira(principal: Principal, text: str, *, store=jira_case, graph,
              extract_fn=None, key: str | None = None) -> Filing:
    extract_fn = extract_fn or extract_jira_fields
    idem = key or idempotency_key(principal.email, text)
    dup = store.get_case_by_idempotency_key(idem)
    if dup is not None and dup["status"] != "draft":
        return _existing("jira", dup)

    fields = extract_fn(text)
    project = fields["project"]
    approver_email = JIRA_PROJECT_APPROVERS.get(project)
    summary = f"{project or 'no project'} · {fields['summary']}"

    case = store.create_case(principal.email, approver_email, project, fields["issue_type"],
                             fields["summary"], fields["description"], idem)
    case_id = str(case["case_id"])
    if case["status"] != "draft":
        return _existing("jira", case, summary)

    detail = {"project": project, "issue_type": fields["issue_type"],
              "summary": fields["summary"], "description": fields["description"]}

    # No project determined, or a known project with nobody mapped to approve it: there is
    # no gate to park at, so the Case ends here. A project OUTSIDE the allowlist falls
    # through to the graph, whose validator ends it at denied_policy — either way, no
    # silent cross-team routing.
    if not project or (project in JIRA_ALLOWED_PROJECTS and approver_email is None):
        reason = "no project determined" if not project else "no approver mapped"
        row = store.transition(case_id, "unroutable", "system", reason)
        return Filing("jira", case_id, row["status"], None, summary, detail, reason)

    row = jira_start_case(graph, case_id=case_id, principal=principal, raw_text=text,
                          approver_email=approver_email)
    approver = approver_email if row["status"] == "pending_approval" else None
    return Filing("jira", case_id, row["status"], approver, summary, detail)


# --- onboarding -----------------------------------------------------------------------

def file_onboarding(principal: Principal, text: str, *, store=onboarding_case, hris, graph,
                    extract_fn=None, validate_fn=None, key: str | None = None) -> Filing:
    extract_fn = extract_fn or extract_onboarding_fields
    validate_fn = validate_fn or validate_onboarding
    idem = key or idempotency_key(principal.email, text)
    dup = store.get_case_by_idempotency_key(idem)
    if dup is not None and dup["status"] != "draft":
        return _existing("onboarding", dup)

    # The ONE extraction for this Case: its output becomes the Case row AND the graph's
    # seeded state, so the tools the manager approves are exactly the tools the connector
    # grants. A model that returns nothing usable ends the request before a Case exists.
    try:
        fields = extract_fn(text)
    except OnboardingExtractError as exc:
        raise ExtractionFailed("Could not understand the request") from exc

    verdict = validate_fn(fields["role"], fields["extra_tools"])
    approver_email = hris.manager_email(principal) if hris else None
    summary = f"{fields['role']} · {len(verdict.tools)} tool(s)"

    case = store.create_case(principal.email, approver_email, fields["role"], verdict.tools,
                             idem)
    case_id = str(case["case_id"])
    if case["status"] != "draft":
        return _existing("onboarding", case, summary)

    detail = {"role": fields["role"], "tools": list(verdict.tools)}
    if not verdict.ok:
        row = store.transition(case_id, "denied_policy", "system",
                               verdict.reason or "validation failed")
        return Filing("onboarding", case_id, row["status"], None, summary, detail,
                      verdict.reason)
    if not approver_email:
        row = store.transition(case_id, "unroutable", "system", "no manager mapped")
        return Filing("onboarding", case_id, row["status"], None, summary, detail,
                      "No manager is mapped to you, so there is nobody to approve this.")

    row = onboarding_start_case(graph, case_id=case_id, principal=principal, raw_text=text,
                                approver_email=approver_email, role=fields["role"],
                                extra_tools=fields["extra_tools"])
    approver = approver_email if row["status"] == "pending_approval" else None
    return Filing("onboarding", case_id, row["status"], approver, summary, detail)


# --- the dispatcher the chat calls ----------------------------------------------------

def file_case(agent: str, principal: Principal, text: str) -> Filing:
    """Agent name -> the filing function, with the collaborators the running process owns
    (the compiled graph comes from the registry, so an agent whose kill switch is off is
    simply not fileable)."""
    from backend.core.hris.mock import MockHRIS
    from backend.services import write_agents

    registered = write_agents.get(agent)
    if registered is None:
        raise UnknownAgentError(f"no write agent {agent!r}")
    graph = registered.graph

    if agent == "leave":
        return file_leave(principal, text, hris=MockHRIS(), graph=graph)
    if agent == "jira":
        return file_jira(principal, text, graph=graph)
    if agent == "onboarding":
        return file_onboarding(principal, text, hris=MockHRIS(), graph=graph)
    raise UnknownAgentError(f"no write agent {agent!r}")
