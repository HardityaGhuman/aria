"""Slack identity map: verified Slack-user -> app-user binding + Principal rebuild.
Live-pg only (@requires_pg); seeds a throwaway user and cleans it up."""
import uuid

from backend.core import users
from backend.tests.conftest_pg import requires_pg


def _seed_user():
    users.initialize_users_table()
    email = f"slacktest-{uuid.uuid4().hex[:8]}@gsvh.test"
    return users.create_user(email, "x", "employee", "us")


def _cleanup(user_id):
    from backend.core.db import connection
    with connection() as conn:
        conn.execute("DELETE FROM slack_identity_map WHERE app_user_id = %s", (user_id,))
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


@requires_pg
def test_link_and_rebuild_principal():
    import backend.core.slack_identity as si
    user = _seed_user()
    try:
        si.initialize_slack_identity_table()
        si.link_slack_user("U-" + str(user["id"]), user["id"], "T1")
        assert si.app_user_id_for_slack("U-" + str(user["id"])) == user["id"]
        p = si.principal_for_slack("U-" + str(user["id"]))
        assert p is not None
        assert p.email == user["email"]
        assert p.role == user["role"]
        assert si.slack_user_for_email(user["email"]) == "U-" + str(user["id"])
    finally:
        _cleanup(user["id"])


@requires_pg
def test_unmapped_slack_user_is_none():
    import backend.core.slack_identity as si
    si.initialize_slack_identity_table()
    assert si.principal_for_slack("U-nope-xyz") is None
    assert si.app_user_id_for_slack("U-nope-xyz") is None


@requires_pg
def test_relink_overwrites():
    import backend.core.slack_identity as si
    user = _seed_user()
    try:
        si.initialize_slack_identity_table()
        sid = "U-" + str(user["id"])
        si.link_slack_user(sid, user["id"], "T1")
        si.link_slack_user(sid, user["id"], "T1")  # idempotent re-link
        assert si.app_user_id_for_slack(sid) == user["id"]
    finally:
        _cleanup(user["id"])
