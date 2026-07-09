"""Slack OAuth linking routes. Hermetic: builds a minimal app, overrides auth, and
stubs the Slack token exchange + identity write (no network, no DB)."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.slack_auth as slack_auth
from backend.core.auth import get_current_user


def _app(with_user=True):
    app = FastAPI()
    app.include_router(slack_auth.router)
    if with_user:
        app.dependency_overrides[get_current_user] = lambda: {"id": 7, "role": "employee", "region": "us",
                                                               "email": "u7@gsvh.test"}
    return app


def test_start_requires_auth():
    client = TestClient(_app(with_user=False))
    r = client.get("/auth/slack/start")
    assert r.status_code == 401


def test_start_returns_authorize_url():
    client = TestClient(_app())
    r = client.get("/auth/slack/start")
    assert r.status_code == 200
    assert "authorize_url" in r.json()
    assert "state=" in r.json()["authorize_url"]


def test_callback_links_mapping(monkeypatch):
    calls = {}
    monkeypatch.setattr(slack_auth, "_exchange_code",
                        lambda code: {"slack_user_id": "U9", "team_id": "T9"})
    monkeypatch.setattr(slack_auth, "link_slack_user",
                        lambda slack_user_id, app_user_id, slack_team_id: calls.update(
                            slack_user_id=slack_user_id, app_user_id=app_user_id, team=slack_team_id))
    client = TestClient(_app())
    authorize_url = client.get("/auth/slack/start").json()["authorize_url"]
    state_val = authorize_url.split("state=")[1].split("&")[0]
    r = client.get(f"/auth/slack/callback?code=abc&state={state_val}")
    assert r.status_code in (200, 302)
    assert calls["slack_user_id"] == "U9"
    assert calls["app_user_id"] == 7
