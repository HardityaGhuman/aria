"""
core/errors.py
--------------
One error shape for the whole API: ``{"error": {"code", "message", "detail"}}``.
A stable, machine-readable body lets the React client branch on ``code`` instead
of parsing prose, and keeps internal exceptions from leaking as raw tracebacks.

``AppError`` is the application's own raisable error (a known, user-facing
failure with an HTTP status). Two framework errors are also normalized to the
same envelope: request validation (422) and any unhandled exception (500).

Note: FastAPI ``HTTPException`` is intentionally left on its default shape so
existing routes/tests keep working; new code should prefer ``AppError``.
"""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI, Request
# pyrefly: ignore [missing-import]
from fastapi.encoders import jsonable_encoder
# pyrefly: ignore [missing-import]
from fastapi.exceptions import RequestValidationError
# pyrefly: ignore [missing-import]
from fastapi.responses import JSONResponse

from backend.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """A known, user-facing failure carrying a stable code + HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 400, detail=None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


def _envelope(code: str, message: str, detail=None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail}}


async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.detail),
    )


async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_envelope(
            "validation_error",
            "Request validation failed.",
            jsonable_encoder(exc.errors()),
        ),
    )


async def _unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Log the real cause server-side; never surface internals to the client.
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_envelope("internal_error", "An unexpected error occurred.", None),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Wire the uniform-envelope handlers onto the app."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
