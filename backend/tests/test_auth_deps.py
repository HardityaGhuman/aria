"""Tests for the FastAPI auth dependencies: get_current_user (401) and
require_role (403). No DB needed — identity comes from the token claims."""
# pyrefly: ignore [missing-import]
from fastapi import Depends, FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

from backend.core import auth

app = FastAPI()


@app.get("/whoami")
def whoami(user: dict = Depends(auth.get_current_user)):
    return user


@app.get("/hr-only")
def hr_only(user: dict = Depends(auth.require_role("hr"))):
    return {"ok": True}


client = TestClient(app)


def _bearer(user_id: int, role: str) -> dict:
    token = auth.create_access_token({"id": user_id, "role": role})
    return {"Authorization": f"Bearer {token}"}


def test_no_token_is_401():
    assert client.get("/whoami").status_code == 401


def test_valid_token_returns_identity():
    resp = client.get("/whoami", headers=_bearer(7, "employee"))
    assert resp.status_code == 200
    assert resp.json() == {"id": 7, "role": "employee", "region": "us", "email": None}


def test_garbage_token_is_401():
    resp = client.get("/whoami", headers={"Authorization": "Bearer garbage"})
    assert resp.status_code == 401


def test_require_role_forbids_wrong_role():
    resp = client.get("/hr-only", headers=_bearer(1, "employee"))
    assert resp.status_code == 403


def test_require_role_allows_matching_role():
    resp = client.get("/hr-only", headers=_bearer(2, "hr"))
    assert resp.status_code == 200


def test_token_with_non_integer_sub_is_401():
    # Validly signed but malformed payload (sub is not an int) must be 401, not 500.
    # pyrefly: ignore [missing-import]
    import jwt
    from backend.core import config

    bad = jwt.encode({"sub": "not-an-int", "role": "hr"}, config.JWT_SECRET, algorithm="HS256")
    resp = client.get("/whoami", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401
