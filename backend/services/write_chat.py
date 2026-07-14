"""services/write_chat.py
----------------------
The chat's view of the write agents: file a Case, list the Cases awaiting the caller's
decision, and turn either into a card + a sentence a human can read.

The replies here are TEMPLATED, not generated. A Case's status, its approver, and what it
will do are facts; handing them to a language model to paraphrase would let a model
hallucinate the state of a pending write, which is exactly the thing this system exists to
never do. The model's only job on a write turn already happened: naming the intent, and
(inside the agent) extracting the fields a deterministic validator then checked.
"""
from backend.core.tools.principal import Principal
from backend.core.write import case_store
from backend.services import write_agents
from backend.services.write_intake import ExtractionFailed, Filing, UnknownAgentError, file_case

__all__ = ["agent_available", "file_case", "list_cases_for_approver", "render_approvals",
           "render_filing", "card_from_filing", "card_from_row", "ExtractionFailed",
           "UnknownAgentError"]


def agent_available(agent: str) -> bool:
    """Is this write agent actually running in this process? An agent whose kill switch is
    off is not registered, and the chat lane must then degrade to a normal read rather than
    promise the user an action nothing can perform."""
    return write_agents.get(agent) is not None


def list_cases_for_approver(email: str) -> list[dict]:
    """The Cases waiting on THIS person. The email predicate is the authorization boundary:
    a caller who approves nothing sees an empty list."""
    return case_store.list_for_user(write_agents.specs(), email, "approver")


# --- cards ----------------------------------------------------------------------------

def card_from_filing(filing: Filing, requester_email: str) -> dict:
    return {
        "case_id": filing.case_id,
        "agent": filing.agent,
        "status": filing.status,
        "summary": filing.summary,
        "requester_email": requester_email,
        "approver_email": filing.approver_email,
        "detail": filing.detail,
        "reason": filing.reason,
        # The requester is never their own approver, so a freshly filed Case is never
        # decidable by the person who just filed it.
        "can_decide": False,
    }


def _row_summary(row: dict) -> str:
    """A one-liner per agent, built from the Case's own business columns — never from the
    raw message (which could carry injected text) and never from a model."""
    agent = row.get("agent")
    if agent == "leave":
        return f"{row.get('start_date')} to {row.get('end_date')} · {row.get('days')} day(s)"
    if agent == "jira":
        return f"{row.get('project')} · {row.get('summary')}"
    if agent == "onboarding":
        tools = row.get("tools") or []
        return f"{row.get('role')} · {len(tools)} tool(s)"
    return row.get("status", "")


def card_from_row(row: dict, caller_email: str) -> dict:
    return {
        "case_id": str(row["case_id"]),
        "agent": row.get("agent", ""),
        "status": row.get("status", ""),
        "summary": _row_summary(row),
        "requester_email": row.get("employee_email"),
        "approver_email": row.get("approver_email"),
        "detail": {k: v for k, v in row.items()
                   if k not in {"case_id", "agent", "status", "employee_email",
                                "approver_email", "created_at", "updated_at"}},
        "reason": row.get("failure_reason"),
        "can_decide": (row.get("status") == "pending_approval"
                       and row.get("approver_email") == caller_email),
    }


# --- prose ----------------------------------------------------------------------------

_AGENT_NOUN = {"leave": "leave request", "jira": "work request",
               "onboarding": "access request"}


def render_filing(filing: Filing) -> str:
    noun = _AGENT_NOUN.get(filing.agent, "request")
    if filing.status == "pending_approval":
        return (f"I've filed your {noun} — **{filing.summary}** — and sent it to "
                f"**{filing.approver_email}** for approval. Nothing is written until they "
                f"approve it. I'll keep the case open; you can ask me about it any time.")
    if filing.status == "denied_policy":
        return (f"I couldn't file that {noun}: {filing.reason or 'it did not pass the rules'}. "
                f"Nothing was submitted.")
    if filing.status == "unroutable":
        return (f"I prepared the {noun} — **{filing.summary}** — but "
                f"{filing.reason or 'there is nobody mapped to approve it'}, so it can't go "
                f"forward. Nothing was written.")
    return f"Your {noun} — **{filing.summary}** — is now **{filing.status}**."


def render_approvals(cards: list[dict]) -> str:
    if not cards:
        return "Nothing is waiting on your approval right now."
    if len(cards) == 1:
        card = cards[0]
        return (f"One request is waiting on you: a {_AGENT_NOUN.get(card['agent'], 'request')} "
                f"from **{card['requester_email']}** — {card['summary']}. Approving it will "
                f"perform the write; denying it closes the case.")
    return (f"**{len(cards)}** requests are waiting on your approval. Approving one performs "
            f"the write; denying it closes the case.")
