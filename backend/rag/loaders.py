"""
rag/loaders.py
--------------
File loading and text extraction. Supports PDF (per-page), Markdown, and plain
text. Markdown/text files may carry a leading YAML-style frontmatter block that
declares the document's ``department`` and ``access_tier``; this module parses
that block, strips it from the body, and stamps the resolved values onto every
returned ``Document`` so the indexer can persist them as Chroma metadata.
"""
import hashlib
import os
import re

# pyrefly: ignore [missing-import]
from langchain_core.documents import Document

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt"}

# Default tier for any document that does not declare one. "all" means every
# authenticated user may retrieve it; the only other tier today is "hr_only".
DEFAULT_ACCESS_TIER = "all"
DEFAULT_REGION = "global"
DEFAULT_DOC_TYPE = "policy"
DEFAULT_VERSION = "2026.1"
DEFAULT_EFFECTIVE_DATE = "2026-01-01"


def file_hash(filepath: str) -> str:
    """SHA-256 of a file's bytes, used to detect changed documents on reindex."""
    digest = hashlib.sha256()
    with open(filepath, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---`` frontmatter block from the body.

    Returns ``(metadata, body)``. The parser is deliberately a flat
    ``key: value`` reader rather than a full YAML dependency — the schema is
    flat (department, access_tier, title) by design. If the file does not start
    with ``---`` or the block never closes, the whole text is treated as body
    with no metadata, so a malformed block can never break loading.
    """
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    closing = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == "---"),
        None,
    )
    if closing is None:
        return {}, text

    metadata: dict = {}
    for line in lines[1:closing]:
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not key:
            continue
        # Drop surrounding quotes and any trailing "# inline comment".
        value = re.sub(r"\s+#.*$", "", value.strip()).strip().strip("\"'")
        metadata[key] = value

    body = "\n".join(lines[closing + 1:]).lstrip("\n")
    return metadata, body


def _load_pdf_file(filepath: str) -> tuple[list[Document], dict]:
    from pypdf import PdfReader

    reader = PdfReader(filepath)
    pages = []
    filename = os.path.basename(filepath)
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if page_text.strip():
            pages.append(
                Document(
                    page_content=f"Page {page_number}\n{page_text}",
                    metadata={"page": page_number, "source": filename},
                )
            )
    # PDFs carry no frontmatter; metadata is resolved from the folder fallback.
    return pages, {}


def _load_text_like(filepath: str) -> tuple[list[Document], dict]:
    """Load a Markdown or plain-text file as a single ``Document``.

    Frontmatter is parsed and stripped so the ``---`` block is never embedded or
    retrieved. The whole document is one ``Document``; structure-aware chunking
    downstream splits it on headings.
    """
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    frontmatter, body = _parse_frontmatter(raw)
    body = clean_text(body)
    if not body:
        return [], frontmatter

    filename = os.path.basename(filepath)
    return [Document(page_content=body, metadata={"source": filename})], frontmatter


def load_document(filepath: str, department_fallback: str = "") -> list[Document]:
    """Load a file into ``Document`` objects, stamped with department + tier.

    ``department_fallback`` is the parent-folder name supplied by the indexer; it
    is used when a document does not declare its own ``department`` in
    frontmatter (always the case for PDFs).
    """
    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        docs, frontmatter = _load_pdf_file(filepath)
    elif extension in {".md", ".txt"}:
        docs, frontmatter = _load_text_like(filepath)
    else:
        return []

    department = frontmatter.get("department") or department_fallback
    access_tier = frontmatter.get("access_tier") or DEFAULT_ACCESS_TIER
    region = frontmatter.get("region") or DEFAULT_REGION
    doc_type = frontmatter.get("doc_type") or DEFAULT_DOC_TYPE
    version = frontmatter.get("version") or DEFAULT_VERSION
    effective_date = frontmatter.get("effective_date") or DEFAULT_EFFECTIVE_DATE
    for doc in docs:
        doc.metadata["department"] = department
        doc.metadata["access_tier"] = access_tier
        doc.metadata["region"] = region
        doc.metadata["doc_type"] = doc_type
        doc.metadata["version"] = version
        doc.metadata["effective_date"] = effective_date
    return docs
