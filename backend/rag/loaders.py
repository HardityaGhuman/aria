"""
rag/loaders.py
--------------
File loading and text extraction. Currently PDF-only; this module is the seam
where additional formats (markdown, txt, html) will be added.
"""
import hashlib
import os
import re

# pyrefly: ignore [missing-import]
from langchain_core.documents import Document

SUPPORTED_EXTENSIONS = {".pdf"}


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


def _load_pdf_file(filepath: str) -> list[Document]:
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
    return pages


def load_document(filepath: str) -> list[Document]:
    """Load a file into a list of per-page ``Document`` objects."""
    extension = os.path.splitext(filepath)[1].lower()
    if extension == ".pdf":
        return _load_pdf_file(filepath)
    return []
