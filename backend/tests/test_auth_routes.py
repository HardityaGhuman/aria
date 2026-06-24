"""Tests for routes/auth.py — login (generic 401, no enumeration) and /auth/me.
DB access is monkeypatched so no Postgres is required."""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

from backend.core import auth, users
from backend.routes.auth import router

app = FastAPI()
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
    assert resp.json() == {"id": 5, "role": "hr"}
