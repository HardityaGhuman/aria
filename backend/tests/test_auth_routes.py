"""Tests for routes/auth.py — login (generic 401, no enumeration) and /auth/me.
DB access is monkeypatched so no Postgres is required."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import pytest

from backend.core import auth, tokens, users
from backend.core.config import FRONTEND_ORIGIN
from backend.core.ratelimit import limiter
from backend.routes.auth import router

app = FastAPI()
app.state.limiter = limiter  # login is rate-limited; slowapi reads app.state
app.include_router(router)
client = TestClient(app)


def _fake_user(monkeypatch, *, email="hr@co.com", password="right-pw", role="hr"):
    row = {
        "id": 5,
        "email": email,
        "password_hash": auth.hash_password(password),
        "role": role,
    }
    monkeypatch.setattr(users, "get_user_by_email", lambda e: row if e == email else None)
    return row


def test_login_success_returns_token_and_role(monkeypatch):
    _fake_user(monkeypatch)
    resp = client.post("/auth/login", json={"email": "hr@co.com", "password": "right-pw"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "hr"
    assert body["token_type"] == "bearer"
    assert auth.decode_token(body["access_token"])["role"] == "hr"


def test_login_wrong_password_is_generic_401(monkeypatch):
    _fake_user(monkeypatch)
    resp = client.post("/auth/login", json={"email": "hr@co.com", "password": "WRONG"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_login_unknown_email_gives_same_401(monkeypatch):
    _fake_user(monkeypatch)
    resp = client.post("/auth/login", json={"email": "ghost@co.com", "password": "x"})
    assert resp.status_code == 401
    # identical message to wrong-password → no user enumeration
    assert resp.json()["detail"] == "Invalid email or password"


def test_me_requires_token():
    assert client.get("/auth/me").status_code == 401


def test_me_returns_identity(monkeypatch):
    _fake_user(monkeypatch)
    login = client.post("/auth/login", json={"email": "hr@co.com", "password": "right-pw"})
    token = login.json()["access_token"]
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"id": 5, "role": "hr", "region": "us", "email": "hr@co.com"}


# --- logout revocation (tighten_plan 3.2) ---

def _db_or_skip():
    try:
        tokens.initialize_tokens_table()
    except tokens.TokenError:
        pytest.skip("Postgres not available for refresh-token store tests")


def test_logout_revokes_refresh_token(monkeypatch):
    """The refresh cookie must be scoped so /auth/logout actually receives it and
    can revoke the token. With the cookie pinned to /auth/refresh the httpx jar
    never sends it here, so logout was a silent no-op."""
    _db_or_skip()
    _fake_user(monkeypatch)
    monkeypatch.setattr(users, "get_user_by_id", lambda i: {"id": 5, "role": "hr", "region": "us"})
    c = TestClient(app)
    login = c.post("/auth/login", json={"email": "hr@co.com", "password": "right-pw"})
    raw = login.cookies.get("refresh_token")
    jti = auth.decode_token(raw)["jti"]
    assert tokens.is_valid(jti) is True

    resp = c.post("/auth/logout", headers={"origin": FRONTEND_ORIGIN})
    assert resp.status_code == 200
    assert tokens.is_valid(jti) is False  # logout received the cookie and revoked it


def test_logout_rejects_foreign_origin(monkeypatch):
    _fake_user(monkeypatch)
    monkeypatch.setattr(users, "get_user_by_id", lambda i: {"id": 5, "role": "hr", "region": "us"})
    c = TestClient(app)
    c.post("/auth/login", json={"email": "hr@co.com", "password": "right-pw"})
    resp = c.post("/auth/logout", headers={"origin": "http://evil.example"})
    assert resp.status_code == 403
