"""
models/response_models.py
-------------------------
Shapes of outgoing API responses. Explicit schemas keep the contract stable for
the frontend and self-document the Swagger UI.
"""
from pydantic import BaseModel, Field


class Source(BaseModel):
    """One cited chunk, in the frozen shape the React client binds to."""

    document_id: str = Field(..., description="Stable doc id = path relative to docs/ (e.g. hr/x.md).")
    file: str = Field(..., description="Human-facing filename (basename of document_id).")
    section: str | None = Field(None, description="Parent section heading, when known.")
    source_type: str | None = Field(None, description="Access tier the chunk came from (all/manager/hr_only).")


class ChatResponse(BaseModel):
    """Standardized envelope for ``POST /chat``.

    ``status`` lets the client distinguish a grounded answer from a graceful
    non-answer without string-matching the prose: ``ok`` (answered),
    ``no_results`` (nothing found), ``blocked`` (RBAC-restricted), ``refused``
    (out of scope).
    """

    answer: str = Field(..., description="The assistant's answer text.")
    sources: list[Source] = Field(
        default_factory=list,
        description="Cited documents behind the answer (empty when not grounded).",
    )
    latency_ms: int = Field(..., description="Server-side time to produce the answer.")
    session_id: str = Field(..., description="The conversation id this reply belongs to.")
    status: str = Field(..., description="ok | no_results | blocked | refused.")


# --- Auth ---


class TokenResponse(BaseModel):
    """Response for ``POST /auth/login`` and ``POST /auth/refresh``."""

    access_token: str = Field(..., description="Short-lived bearer access token.")
    token_type: str = Field("bearer", description="Always 'bearer'.")
    role: str = Field(..., description="Caller's role (employee/manager/hr).")
    region: str = Field(..., description="Caller's home region (us/india).")


class MeResponse(BaseModel):
    """Response for ``GET /auth/me``."""

    id: int = Field(..., description="User id.")
    role: str = Field(..., description="Caller's role.")
    region: str = Field(..., description="Caller's home region.")
    email: str | None = Field(None, description="Caller's email, when present in the token.")


class MessageResponse(BaseModel):
    """Generic ``{"message": ...}`` acknowledgement."""

    message: str = Field(..., description="Human-readable result.")


class HistoryResponse(BaseModel):
    """Response for ``GET /chat/history/{session_id}``."""

    session_id: str = Field(..., description="The session the history belongs to.")
    history: list[dict] = Field(default_factory=list, description="Ordered {role, content} messages.")


# --- Preferences ---


class PreferencesResponse(BaseModel):
    """Response for ``GET/PUT /me/preferences`` (always complete via defaults)."""

    tone: str = Field(..., description="Preferred tone.")
    response_length: str = Field(..., description="Preferred answer length.")
    language: str = Field(..., description="Preferred answer language.")


# --- Sessions ---


class SessionInfo(BaseModel):
    """One conversation owned by the caller (list item for the sidebar)."""

    session_id: str = Field(..., description="Conversation id.")
    title: str | None = Field(None, description="User-set title, if any.")
    updated_at: str | None = Field(None, description="Last activity (ISO 8601).")


class CreateSessionResponse(BaseModel):
    """Response for ``POST /chat/sessions``."""

    session_id: str = Field(..., description="The newly created conversation id.")


# --- Admin document lifecycle ---


class DocumentInfo(BaseModel):
    """One corpus document, merging on-disk facts with ingestion status."""

    document_id: str = Field(..., description="Path relative to docs/ (stable id), e.g. hr/employment-basics.md.")
    department: str = Field(..., description="Top-level folder the document lives under.")
    type: str = Field(..., description="File extension without the dot (md/pdf/csv/...).")
    size_bytes: int = Field(..., description="File size on disk.")
    status: str = Field(..., description="queued | processing | indexed | failed | unknown.")
    error: str | None = Field(None, description="Failure reason when status is failed.")
    updated_at: str | None = Field(None, description="When the status last changed (ISO 8601).")


class DocumentStatusResponse(BaseModel):
    """Response for ``GET /admin/documents/{document_id}/status``."""

    document_id: str = Field(..., description="Path relative to docs/.")
    status: str = Field(..., description="queued | processing | indexed | failed.")
    error: str | None = Field(None, description="Failure reason when status is failed.")
    updated_at: str | None = Field(None, description="When the status last changed (ISO 8601).")


class UploadResponse(BaseModel):
    """Response for ``POST /admin/documents/upload``."""

    document_id: str = Field(..., description="Path relative to docs/ assigned to the upload.")
    status: str = Field(..., description="Initial status — always 'queued'; indexing runs in the background.")


class DeleteResponse(BaseModel):
    """Response for ``DELETE /admin/documents/{document_id}``."""

    document_id: str = Field(..., description="The document that was removed.")
    deleted_chunks: int = Field(..., description="Number of vector chunks removed from the index.")


class ReindexResponse(BaseModel):
    """Response for ``POST /admin/reindex``."""

    indexed: int = Field(..., description="Chunks added in this run.")
    skipped: int = Field(..., description="Files unchanged and skipped.")
    deleted: int = Field(..., description="Stale chunks removed before re-adding.")
