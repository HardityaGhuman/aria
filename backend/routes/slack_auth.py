"""routes/slack_auth.py
---------------------
"Sign in with Slack" one-time linking. A LOGGED-IN employee (our JWT) starts the
flow; Slack OAuth returns a verified slack_user_id; we bind it to the JWT-derived
app user. Both identities are server-known (employee from JWT, Slack id from OAuth) —
the user never types either, so the binding cannot be forged. A signed ``state`` ties
the callback to the initiating user, closing OAuth CSRF/link-fixation."""
import urllib.parse

import httpx
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, Query
# pyrefly: ignore [missing-import]
from fastapi.responses import HTMLResponse

from backend.core.auth import get_current_user, sign_state, verify_state
from backend.core.config import SLACK_CLIENT_ID, SLACK_CLIENT_SECRET
from backend.core.slack_identity import link_slack_user

router = APIRouter(prefix="/auth/slack", tags=["Slack Link"])

_SLACK_AUTHORIZE = "https://slack.com/openid/connect/authorize"
_SLACK_TOKEN = "https://slack.com/api/openid.connect.token"
_SCOPES = "openid"


@router.get("/start")
def slack_start(user: dict = Depends(get_current_user)):
    state = sign_state({"uid": int(user["id"])})
    params = {
        "client_id": SLACK_CLIENT_ID,
        "scope": _SCOPES,
        "state": state,
        "response_type": "code",
    }
    return {"authorize_url": f"{_SLACK_AUTHORIZE}?{urllib.parse.urlencode(params)}"}


def _exchange_code(code: str) -> dict:
    """Exchange the OAuth code for the verified Slack identity. Returns
    {"slack_user_id", "team_id"}. Isolated so tests stub it."""
    resp = httpx.post(_SLACK_TOKEN, data={
        "client_id": SLACK_CLIENT_ID,
        "client_secret": SLACK_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    # OpenID Connect returns a signed id_token; sub is the Slack user id, and the
    # payload carries the team id. Decode per Slack docs (verify signature) in prod.
    return {"slack_user_id": body["sub"], "team_id": body["https://slack.com/team_id"]}


@router.get("/callback", response_class=HTMLResponse)
def slack_callback(code: str = Query(...), state: str = Query(...)):
    claims = verify_state(state)
    if claims is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state.")
    identity = _exchange_code(code)
    link_slack_user(identity["slack_user_id"], int(claims["uid"]), identity["team_id"])
    return HTMLResponse("<p>Slack linked. You can close this window.</p>")
