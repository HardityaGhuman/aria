"""core/slack_identity.py
-----------------------
Verified Slack-user -> app-user binding. This is how a Slack ``/leave`` request
gets a server-built Principal WITHOUT trusting anything the user typed: the inbound
Slack signature makes ``slack_user_id`` trustworthy, this table maps it to our
``users.id``, and the Principal is rebuilt from the users row (email/role/region).

Extends the identity invariant to "JWT OR verified-Slack-mapping"; it never weakens
it — identity is still server-established, never from LLM/user args."""
from dataclasses import dataclass

from backend.core import db
from backend.core.tools.principal import Principal
from backend.core.users import get_user_by_id


@dataclass
class SlackIdentityError(Exception):
    message: str


def _connect():
    return db.pooled(lambda: SlackIdentityError(
        "Could not connect to PostgreSQL for the Slack identity map."
    ))


def initialize_slack_identity_table() -> None:
    """Create slack_identity_map if absent. Idempotent."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_identity_map (
                    slack_user_id  TEXT PRIMARY KEY,
                    app_user_id    BIGINT NOT NULL REFERENCES users(id),
                    slack_team_id  TEXT NOT NULL,
                    linked_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (app_user_id, slack_team_id)
                )
                """
            )


def link_slack_user(slack_user_id: str, app_user_id: int, slack_team_id: str) -> None:
    """Bind (or re-bind) a Slack user to an app user. Server-verified inputs only."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO slack_identity_map (slack_user_id, app_user_id, slack_team_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (slack_user_id)
                DO UPDATE SET app_user_id = EXCLUDED.app_user_id,
                              slack_team_id = EXCLUDED.slack_team_id,
                              linked_at = now()
                """,
                (slack_user_id, app_user_id, slack_team_id),
            )


def app_user_id_for_slack(slack_user_id: str) -> int | None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT app_user_id FROM slack_identity_map WHERE slack_user_id = %s",
                (slack_user_id,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else None


def slack_user_for_email(email: str) -> str | None:
    """Reverse lookup: the Slack id for an app user identified by email — used to
    route the approval message to the manager's Slack."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.slack_user_id
                FROM slack_identity_map m
                JOIN users u ON u.id = m.app_user_id
                WHERE u.email = %s
                """,
                (email,),
            )
            row = cursor.fetchone()
            return row[0] if row else None


def principal_for_slack(slack_user_id: str) -> Principal | None:
    """Rebuild a full server Principal from the mapped users row, or None if the
    Slack user is unmapped or the app user no longer exists."""
    app_user_id = app_user_id_for_slack(slack_user_id)
    if app_user_id is None:
        return None
    user = get_user_by_id(app_user_id)
    if user is None:
        return None
    return Principal(
        user_id=int(user["id"]),
        email=user.get("email"),
        role=user["role"],
        region=user.get("region", "us"),
    )
