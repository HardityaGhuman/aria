"""
routes/admin.py
---------------
HR-only document lifecycle. All routes are gated by ``require_role("hr")``.

Surface:
    POST   /admin/documents/upload               add a source document (multipart)
    GET    /admin/documents                       list corpus docs + ingestion status
    GET    /admin/documents/{document_id}/status  one document's ingestion status
    DELETE /admin/documents/{document_id}         remove a document + its chunks
    POST   /admin/reindex                         rebuild the index from the corpus

``document_id`` is the path relative to the docs root (e.g. ``hr/employment-basics.md``),
the same stable id used as the Chroma ``source`` metadata. Ingestion is asynchronous:
upload returns immediately with ``queued`` and a BackgroundTask drives
``queued -> processing -> indexed|failed`` so a slow embed never blocks the request.
"""
import os

# pyrefly: ignore [missing-import]
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from backend.core.auth import require_role
from backend.core.config import DOCS_PATH, MAX_UPLOAD_BYTES, RATE_LIMIT_ADMIN
from backend.core.ratelimit import limiter
from backend.core.doc_status import (
    all_statuses,
    delete_status,
    get_status,
    set_status,
)
from backend.core.logging import get_logger
from backend.models.response_models import (
    DeleteResponse,
    DocumentInfo,
    DocumentStatusResponse,
    ReindexResponse,
    UploadResponse,
)
from backend.rag.indexing import (
    delete_document_chunks,
    initialize_vectorstore,
    list_policy_documents,
)
from backend.rag.loaders import SUPPORTED_EXTENSIONS
from backend.rag.vector_repository import indexed_sources

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["Admin"])

# Allow-lists for the metadata an HR uploader may stamp on a document. Kept in
# lock-step with the retrieval filters: access_tier mirrors auth._ROLE_TIERS,
# region mirrors auth.regions_for_user, status mirrors the loader's status field.
VALID_ACCESS_TIERS = {"all", "manager", "hr_only"}
VALID_REGIONS = {"global", "us", "india"}
VALID_STATUSES = {"active", "superseded"}


def _write_sidecar(target: str, *, department: str, access_tier: str, region: str, status: str) -> None:
    """Write a ``<target>.meta.yaml`` sidecar carrying the uploader's metadata.

    Every uploaded format reads this sidecar (the loader merges it; inline md/txt
    frontmatter still wins). This is the single place portal-set tier/region/status
    is persisted, so an HR upload can tier a csv/xlsx/pdf that has no inline block.
    """
    lines = [
        f"department: {department}",
        f"access_tier: {access_tier}",
        f"region: {region}",
        f"status: {status}",
    ]
    with open(target + ".meta.yaml", "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _resolve_under_docs(rel_path: str) -> str:
    """Resolve ``rel_path`` against DOCS_PATH and refuse anything that escapes it.

    Path-traversal guard: a crafted ``../../etc/passwd`` document_id must never
    resolve outside the corpus root, whether reading, deleting, or writing.
    """
    docs_root = os.path.realpath(DOCS_PATH)
    target = os.path.realpath(os.path.join(docs_root, rel_path))
    if target != docs_root and not target.startswith(docs_root + os.sep):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document path.",
        )
    return target


def _index_document(rel_path: str) -> None:
    """Background worker: reindex the corpus and record this doc's final status.

    initialize_vectorstore() is hash-skip aware, so it only embeds the changed
    (just-uploaded) file; the rest of the corpus is skipped cheaply.
    """
    try:
        set_status(rel_path, "processing")
        initialize_vectorstore()
        set_status(rel_path, "indexed")
        logger.info("Indexed uploaded document %s", rel_path)
    except Exception as exc:  # noqa: BLE001 — record any failure, don't crash the worker
        logger.exception("Ingestion failed for %s", rel_path)
        set_status(rel_path, "failed", error=str(exc))


@router.post("/documents/upload", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit(RATE_LIMIT_ADMIN)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    department: str = Form(...),
    access_tier: str = Form("all"),
    region: str = Form("global"),
    doc_status: str = Form("active"),
    _: dict = Depends(require_role("hr")),
):
    """Accept a document, store it under ``docs/<department>/``, queue ingestion.

    ``access_tier``/``region``/``doc_status`` are stamped into a ``.meta.yaml``
    sidecar so the uploader can tier any format (csv/xlsx/pdf carry no inline
    frontmatter); for md/txt an inline frontmatter block still takes precedence.
    """
    filename = os.path.basename(file.filename or "")
    if not filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing filename.")

    extension = os.path.splitext(filename)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unsupported file type {extension!r}. Allowed: {sorted(SUPPORTED_EXTENSIONS)}.",
        )

    # department is a single path segment — reject slashes / traversal outright.
    dept = department.strip()
    if not dept or "/" in dept or "\\" in dept or dept.startswith("."):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid department.")

    # Validate the metadata against the same allow-lists the retrieval filters use.
    # A bad tier must never reach Chroma metadata where it could mis-gate access.
    access_tier = access_tier.strip()
    region = region.strip()
    doc_status = doc_status.strip()
    if access_tier not in VALID_ACCESS_TIERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid access_tier. Allowed: {sorted(VALID_ACCESS_TIERS)}.")
    if region not in VALID_REGIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid region. Allowed: {sorted(VALID_REGIONS)}.")
    if doc_status not in VALID_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid status. Allowed: {sorted(VALID_STATUSES)}.")

    rel_path = f"{dept}/{filename}"
    target = _resolve_under_docs(rel_path)

    # Don't silently clobber an existing policy. Updating a doc is an explicit
    # delete-then-upload, so a same-name collision is almost always a mistake.
    if os.path.exists(target):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A document already exists at {rel_path!r}. Delete it first to replace it.",
        )

    # Bounded read: stream in chunks and abort past the cap so an oversized upload
    # can't buffer unbounded into memory (DoS). UploadFile.size is advisory, so we
    # enforce on the actual bytes read.
    contents = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        contents.extend(chunk)
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"File exceeds the {MAX_UPLOAD_BYTES} byte upload limit.",
            )

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(contents)
    _write_sidecar(target, department=dept, access_tier=access_tier, region=region, status=doc_status)

    set_status(rel_path, "queued")
    background_tasks.add_task(_index_document, rel_path)
    logger.info("Queued uploaded document %s (%d bytes)", rel_path, len(contents))
    return UploadResponse(document_id=rel_path, status="queued")


@router.get("/documents", response_model=list[DocumentInfo])
def list_documents(_: dict = Depends(require_role("hr"))):
    """List every corpus document, merging on-disk facts with ingestion status.

    Status precedence: a ``document_status`` row (live lifecycle: queued/
    processing/failed from the admin upload path) wins. Docs with no row are the
    offline-indexed seed corpus, which the tracking table never sees — for those we
    ask Chroma directly: chunks present => ``indexed``, else ``unknown``. Without
    this, the whole seed corpus showed ``unknown`` despite being fully searchable."""
    statuses = all_statuses()
    try:
        indexed = indexed_sources()
    except Exception:
        # A status nicety must never break the list; fall back to "unknown".
        logger.warning("Could not read indexed sources from Chroma", exc_info=True)
        indexed = set()
    documents = []
    for doc in list_policy_documents():
        rel_path = doc["filename"]
        st = statuses.get(rel_path)
        if st:
            doc_status, error = st["status"], st.get("error")
        else:
            doc_status = "indexed" if rel_path in indexed else "unknown"
            error = None
        documents.append(
            DocumentInfo(
                document_id=rel_path,
                department=doc["department"],
                type=doc["type"],
                size_bytes=doc["size_bytes"],
                status=doc_status,
                error=error,
                updated_at=st["updated_at"].isoformat() if st and st.get("updated_at") else None,
            )
        )
    return documents


@router.get("/documents/{document_id:path}/status", response_model=DocumentStatusResponse)
def document_status(document_id: str, _: dict = Depends(require_role("hr"))):
    """Return one document's ingestion status."""
    _resolve_under_docs(document_id)  # validate path shape even though we only read DB
    st = get_status(document_id)
    if not st:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No status for that document.")
    return DocumentStatusResponse(
        document_id=document_id,
        status=st["status"],
        error=st.get("error"),
        updated_at=st["updated_at"].isoformat() if st.get("updated_at") else None,
    )


@router.delete("/documents/{document_id:path}", response_model=DeleteResponse)
def delete_document(document_id: str, _: dict = Depends(require_role("hr"))):
    """Remove a document: its vector chunks, the file on disk, and its status row."""
    target = _resolve_under_docs(document_id)
    removed = delete_document_chunks(document_id)
    if os.path.exists(target):
        os.remove(target)
    # Remove the companion metadata sidecar too, so a re-upload starts clean.
    if os.path.exists(target + ".meta.yaml"):
        os.remove(target + ".meta.yaml")
    delete_status(document_id)
    logger.info("Deleted document %s (%d chunks)", document_id, removed)
    return DeleteResponse(document_id=document_id, deleted_chunks=removed)


@router.post("/reindex", response_model=ReindexResponse)
@limiter.limit(RATE_LIMIT_ADMIN)
def reindex(request: Request, _: dict = Depends(require_role("hr"))):
    """Rebuild the index from the corpus (hash-skip aware; only changed files re-embed)."""
    try:
        stats = initialize_vectorstore()
    except Exception:
        logger.exception("Reindex failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Reindex failed. Check server logs.",
        )
    logger.info("Reindex complete: %s", stats)
    return ReindexResponse(**stats)
