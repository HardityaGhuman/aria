"""Tests for refresh tokens, token-type enforcement, and the refresh-token store."""
import pytest
# pyrefly: ignore [missing-import]
from fastapi import HTTPException
# pyrefly: ignore [missing-import]
from fastapi.security import HTTPAuthorizationCredentials

from backend.core import auth, tokens

USER = {"id": 1, "role": "employee", "region": "us"}


def _creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


# --- token type enforcement (no DB) ---

def test_access_token_accepted_by_get_current_user():
    token = auth.create_access_token(USER)
    user = auth.get_current_user(_creds(token))
    assert user == {"id": 1, "role": "employee", "region": "us", "email": None}


def test_refresh_token_rejected_as_access_credential():
    refresh, _jti, _exp = auth.create_refresh_token(USER)
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(_creds(refresh))
    assert exc.value.status_code == 401


def test_refresh_token_carries_type_and_jti():
    refresh, jti, _exp = auth.create_refresh_token(USER)
    claims = auth.decode_token(refresh)
    assert claims["type"] == "refresh"
    assert claims["jti"] == jti
    assert claims["sub"] == "1"


def test_access_token_carries_access_type():
    claims = auth.decode_token(auth.create_access_token(USER))
    assert claims["type"] == "access"


# --- refresh-token store (needs Postgres; skipped if unavailable) ---

def _db_or_skip():
    try:
        tokens.initialize_tokens_table()
    except tokens.TokenError:
        pytest.skip("Postgres not available for refresh-token store tests")


def test_store_rotation_and_revoke():
    _db_or_skip()
    _refresh, jti, expires_at = auth.create_refresh_token(USER)
    tokens.store_refresh(jti, 1, expires_at)
    assert tokens.is_valid(jti) is True
    tokens.revoke(jti)  # rotation revokes the old token
    assert tokens.is_valid(jti) is False


def test_revoke_all_invalidates_every_token_for_user():
    _db_or_skip()
    _r1, j1, e1 = auth.create_refresh_token(USER)
    _r2, j2, e2 = auth.create_refresh_token(USER)
    tokens.store_refresh(j1, 999, e1)
    tokens.store_refresh(j2, 999, e2)
    tokens.revoke_all(999)
    assert tokens.is_valid(j1) is False
    assert tokens.is_valid(j2) is False


# --- atomic consume (rotation race fix, tighten_plan 3.1) ---

def test_consume_returns_owner_and_invalidates_in_one_call():
    _db_or_skip()
    _r, jti, exp = auth.create_refresh_token(USER)
    tokens.store_refresh(jti, 4242, exp)
    assert tokens.consume(jti) == 4242
    # The same jti can never be consumed or validated again.
    assert tokens.consume(jti) is None
    assert tokens.is_valid(jti) is False


def test_consume_unknown_or_revoked_returns_none():
    _db_or_skip()
    _r, jti, exp = auth.create_refresh_token(USER)
    tokens.store_refresh(jti, 7, exp)
    tokens.revoke(jti)
    assert tokens.consume(jti) is None
    assert tokens.consume("never-issued-jti") is None


def test_concurrent_consume_yields_exactly_one_winner():
    """Two racers presenting the same refresh jti: exactly one gets the owner,
    the other gets None. This is the guarantee a check-then-act (is_valid then
    revoke) cannot make."""
    _db_or_skip()
    import concurrent.futures

    _r, jti, exp = auth.create_refresh_token(USER)
    tokens.store_refresh(jti, 555, exp)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: tokens.consume(jti), range(8)))

    winners = [r for r in results if r is not None]
    assert winners == [555]  # exactly one winner, everyone else None
