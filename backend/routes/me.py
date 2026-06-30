"""
routes/me.py
------------
The caller's own account surface. Today: read/update durable preferences that
shape every answer. Auth-gated at the router level (any role).
"""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException

from backend.core.auth import get_current_user
from backend.core.preferences import (
    PreferencesError,
    get_preferences,
    set_preferences,
)
from backend.models.request_models import UpdatePreferencesRequest
from backend.models.response_models import PreferencesResponse

router = APIRouter(prefix="/me", tags=["Me"], dependencies=[Depends(get_current_user)])


@router.get("/preferences", response_model=PreferencesResponse)
def read_preferences(user: dict = Depends(get_current_user)):
    """Return the caller's preferences (defaults applied when unset)."""
    try:
        prefs = get_preferences(user["id"])
    except PreferencesError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return PreferencesResponse(**prefs)


@router.put("/preferences", response_model=PreferencesResponse)
def update_preferences(body: UpdatePreferencesRequest, user: dict = Depends(get_current_user)):
    """Upsert the caller's preferences and return the merged result."""
    try:
        prefs = set_preferences(
            user["id"], body.tone, body.response_length, body.language
        )
    except PreferencesError as e:
        raise HTTPException(status_code=503, detail=e.message)
    return PreferencesResponse(**prefs)
