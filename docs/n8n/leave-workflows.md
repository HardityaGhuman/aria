# n8n — Slack Leave Self-Service transport edge

n8n is the **transport edge only**. It verifies the Slack signature at the boundary,
relays OAuth, posts messages, and forwards button clicks. It makes **no** authz,
validation, or write decision — all of that lives in our FastAPI + LangGraph Case.
n8n is swappable: a future direct Slack integration would replace these flows without
touching the Case.

All calls into our API carry:

| Header | Value | Purpose |
|---|---|---|
| `Authorization` | `Bearer <N8N_SHARED_SECRET>` | authenticate n8n → our API |
| `X-Slack-Signature` | `v0=…` (from Slack) | our API re-verifies it |
| `X-Slack-Request-Timestamp` | Slack's ts | replay window (±300s) |

Our API is gated by `LEAVE_AGENT_ENABLED`; when false every route below is 404.

## Flows

1. **`/leave` slash command → start a Case.**
   Slack `/leave "Aug 12–14 vacation"` → n8n Slack trigger (verifies Slack signing
   signature) → `POST /agents/leave` with body `{"slack_user_id": "<verified>",
   "text": "<raw request>"}`. n8n does **no** date parsing — the graph's `extract`
   node does. Response: `{case_id, status, approver_slack_user_id}`.

2. **OAuth link relay.**
   The React portal's "Link Slack" button hits `GET /auth/slack/start` (employee's
   JWT) → returns an `authorize_url`. Slack redirects back to
   `GET /auth/slack/callback?code=…&state=…`, which writes the `slack_identity_map`
   row. n8n only relays the redirect if it fronts the callback host.

3. **Approval message → the manager.**
   On `status == "pending_approval"`, n8n posts an interactive Approve/Deny message
   to the manager's Slack (the `approver_slack_user_id` from flow 1's response).

4. **Approve/Deny click → resume the Case.**
   Manager clicks → Slack interaction → n8n (verifies signature) →
   `POST /agents/leave/{case_id}/decision` with body `{"slack_user_id": "<clicker>",
   "decision": "approve"|"deny"}`. Our API re-checks the clicker's linked identity ==
   the Case's `approver_email` before resuming. Response: `{case_id, status,
   confirmation_id}`.

5. **Notifications.**
   n8n posts the terminal outcome (`booked` + confirmation, `denied_policy`/
   `denied_manager` + reason, `write_failed`, `unroutable`) back to the employee (and
   manager where relevant). These are display-only; the Case status is authoritative
   in our DB + audit log.

## Boundary reminder

- Identity is **server-established**: our API resolves the `Principal` from the
  verified `slack_user_id` via `slack_identity_map`; n8n never asserts identity.
- The write (`submit_leave`) is reachable **only** from the graph `book` node after a
  verified manager approval — n8n cannot trigger a booking directly.
