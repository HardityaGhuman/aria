"""
routes/auth.py
--------------
Authentication endpoints: login (issues a JWT) and /auth/me. Login returns a
generic error for both unknown-email and wrong-password so it cannot be used to
enumerate accounts.
"""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field

from backend.core import users
from backend.core.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

# Verified against this when the email is unknown, so response timing does not
# reveal whether an account exists.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    # Capped at 72 bytes: bcrypt silently ignores anything beyond that, so a
    # longer value would give a false sense of strength.
    password: str = Field(min_length=1, max_length=72)


@router.post("/login")
def login(body: LoginRequest):
    try:
        user = users.get_user_by_email(body.email)
    except users.UserError:
        logger.exception("Login failed: users store unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service temporarily unavailable",
        )

    if user is None:
        verify_password(body.password, _DUMMY_HASH)  # equalize timing
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(user)
    return {"access_token": token, "token_type": "bearer", "role": user["role"], "region": user.get("region", "us")}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user
