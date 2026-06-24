"""
routes/admin.py
---------------
HR-only administrative endpoints. /admin/reindex rebuilds the vector index from
the policy corpus; it is gated by require_role("hr"). The reindex runs
synchronously — fine for a few local docs; a background job is the known upgrade
for the serverless step.
"""
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status

from backend.core.auth import require_role
from backend.core.logging import get_logger
from backend.rag.indexing import initialize_vectorstore

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/reindex")
def reindex(_: dict = Depends(require_role("hr"))):
    try:
        stats = initialize_vectorstore()
    except Exception:
        logger.exception("Reindex failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reindex failed. Check server logs.",
        )
    logger.info("Reindex complete: %s", stats)
    return stats
