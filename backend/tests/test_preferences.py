"""Tests for user preferences (Task 6): the prompt builder, the DB round-trip,
and the /me/preferences routes."""
import pytest
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

from backend.core import preferences as prefs
from backend.core.auth import get_current_user


# --- prompt builder (no DB) ---

def test_format_preferences_empty_for_defaults():
    assert prefs.format_preferences(dict(prefs.DEFAULTS)) == ""


def test_format_preferences_includes_custom_values():
    block = prefs.format_preferences(
        {"tone": "friendly", "response_length": "short", "language": "French"}
    )
    assert "tone=friendly" in block
    assert "length=short" in block
    assert "language=French" in block


# --- DB round-trip (needs Postgres; skipped if unavailable) ---

def _db_or_skip():
    try:
        prefs.initialize_preferences_table()
    except prefs.PreferencesError:
        pytest.skip("Postgres not available for preferences tests")


def test_defaults_when_unset():
    _db_or_skip()
    assert prefs.get_preferences(98765) == prefs.DEFAULTS


def test_set_then_get_round_trips():
    _db_or_skip()
    out = prefs.set_preferences(98766, tone="formal", response_length="long", language="Hindi")
    assert out == {"tone": "formal", "response_length": "long", "language": "Hindi"}
    assert prefs.get_preferences(98766) == out


# --- routes (DB monkeypatched) ---

def test_me_preferences_put_then_get(monkeypatch):
    import backend.routes.me as me

    store: dict = {}
    monkeypatch.setattr(me, "set_preferences",
                        lambda uid, tone, rlen, lang: store.setdefault(uid, {}).update(
                            {"tone": tone or "neutral", "response_length": rlen or "medium",
                             "language": lang or "English"}) or store[uid])
    monkeypatch.setattr(me, "get_preferences",
                        lambda uid: store.get(uid, dict(prefs.DEFAULTS)))

    app = FastAPI()
    app.include_router(me.router)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "employee", "region": "us"}
    client = TestClient(app)

    put = client.put("/me/preferences", json={"tone": "friendly", "response_length": "short", "language": "English"})
    assert put.status_code == 200
    assert put.json()["tone"] == "friendly"

    got = client.get("/me/preferences")
    assert got.status_code == 200
    assert got.json()["response_length"] == "short"
