"""Tests for core/auth.py — password hashing and JWT tokens."""
import pytest

from backend.core import auth


# --- password hashing ---

def test_hash_and_verify_round_trip():
    hashed = auth.hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert auth.verify_password("s3cret-pw", hashed) is True


def test_verify_rejects_wrong_password():
    hashed = auth.hash_password("s3cret-pw")
    assert auth.verify_password("wrong-pw", hashed) is False


# --- JWT tokens ---

def test_token_round_trip_carries_sub_and_role():
    token = auth.create_access_token({"id": 42, "role": "hr"})
    claims = auth.decode_token(token)
    assert claims["sub"] == "42"
    assert claims["role"] == "hr"


def test_decode_rejects_tampered_token():
    token = auth.create_access_token({"id": 1, "role": "employee"})
    tampered = token[:-3] + ("xyz" if not token.endswith("xyz") else "abc")
    with pytest.raises(auth.AuthError):
        auth.decode_token(tampered)


def test_decode_rejects_expired_token():
    token = auth.create_access_token({"id": 1, "role": "employee"}, expires_in_hours=-1)
    with pytest.raises(auth.AuthError):
        auth.decode_token(token)


def test_decode_rejects_garbage():
    with pytest.raises(auth.AuthError):
        auth.decode_token("not-a-jwt")


# --- RBAC: role -> visible access tiers (3 tiers: all | manager | hr_only) ---

def test_employee_sees_only_all_tier():
    assert auth.tiers_for_role("employee") == ["all"]


def test_manager_sees_all_and_manager_not_hr():
    tiers = auth.tiers_for_role("manager")
    assert "all" in tiers and "manager" in tiers
    assert "hr_only" not in tiers


def test_hr_sees_every_tier():
    tiers = auth.tiers_for_role("hr")
    assert set(tiers) == {"all", "manager", "hr_only"}


def test_unknown_role_defaults_to_all_only():
    assert auth.tiers_for_role("ceo") == ["all"]
    assert auth.tiers_for_role(None) == ["all"]
