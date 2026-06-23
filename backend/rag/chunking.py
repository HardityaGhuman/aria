"""
rag/chunking.py
---------------
Structure-aware chunking.

A document describes its own structure: we detect table-of-contents pages, use
their titles as section-boundary anchors in the body, split on those headings,
fold tiny heading/intro blurbs forward, and fall back to recursive splitting for
documents with no detectable structure.
"""
import re

# pyrefly: ignore [missing-import]
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.rag.loaders import clean_text

CHUNK_SIZE = 2000
CHUNK_OVERLAP = 100
# Bump to force a one-time reindex when chunking logic changes.
CHUNK_VERSION = "2026-06-23-multisource-md-v6"
# Sections shorter than this are treated as heading/intro blurbs ("In this
# section we explain...") and folded forward into the next real section, so they
# don't survive as low-content chunks that win retrieval slots without payload.
THIN_SECTION_CHARS = 250

SECTION_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)

# Structured heading prefixes: Roman numerals (IV.), decimal (1. / 2.3), lettered (A.)
_HEADING_PREFIX = re.compile(r"^(?:[IVXLCDM]+\.|\d+(?:\.\d+)*\.?|[A-Z]\.)\s+\S")
# Markdown ATX headings (# .. ###### Title). Markdown docs (the policy corpus)
# carry no TOC page, so these are the primary section boundaries for them.
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+\S")


def _normalize_heading(line: str) -> str:
    """Strip Markdown ``#`` markers so the parent_section label is clean text."""
    return line.strip().lstrip("#").strip()


def _looks_like_toc(page_text: str) -> bool:
    """Heuristically decide whether a page is a table of contents.

    Document-agnostic: a TOC is a page whose lines are mostly "<title> <page no>"
    entries, or one that explicitly says "table of contents".
    """
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    if len(lines) < 5:
        return False
    if "table of contents" in " ".join(lines[:3]).lower():
        return True
    # TOC entries end in a page number, often after dotted leaders.
    entry = re.compile(r".+\S[\s.]+\d{1,4}$")
    matches = sum(1 for line in lines if entry.match(line))
    return matches / len(lines) >= 0.6


def _extract_toc_titles(toc_text: str) -> set[str]:
    """Pull section titles out of TOC lines (dropping trailing page numbers)."""
    titles = set()
    for line in toc_text.splitlines():
        line = line.strip()
        match = re.match(r"^(.*\S)[\s.]+\d{1,4}$", line)
        title = (match.group(1) if match else line).strip(" .")
        if len(title) > 2 and not title.isdigit():
            titles.add(title.lower())
    return titles


def _is_heading(line: str, toc_titles: set[str]) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _MARKDOWN_HEADING.match(stripped):
        return True
    if stripped.lower() in toc_titles:
        return True
    return bool(_HEADING_PREFIX.match(stripped))


def _split_into_sections(content: str, toc_titles: set[str]) -> list[tuple[str, str]]:
    """Split body text at detected headings into (heading, text) pairs."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in content.split("\n"):
        if _is_heading(line, toc_titles):
            if buffer:
                sections.append((heading, "\n".join(buffer).strip()))
            heading = _normalize_heading(line)
            buffer = [line]
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer).strip()))
    return [(h, text) for h, text in sections if text]


def _merge_tiny_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Fold heading-only / very short sections forward into the next one so we
    don't emit tiny, low-signal chunks."""
    merged: list[tuple[str, str]] = []
    carry: tuple[str, str] | None = None
    for heading, text in sections:
        if carry:
            heading, text = (carry[0] or heading), carry[1] + "\n\n" + text
            carry = None
        if len(text) < THIN_SECTION_CHARS:
            carry = (heading, text)
        else:
            merged.append((heading, text))
    if carry:
        if merged:
            prev_heading, prev_text = merged[-1]
            merged[-1] = (prev_heading, prev_text + "\n\n" + carry[1])
        else:
            merged.append(carry)
    return merged


def _add_section_chunks(section: str, chunks: list[Document], content_type: str, parent_section: str = ""):
    metadata = {"content_type": content_type, "parent_section": parent_section}
    if len(section) <= CHUNK_SIZE:
        chunks.append(Document(page_content=section, metadata=metadata))
        return

    split_docs = SECTION_SPLITTER.split_documents(
        [Document(page_content=section, metadata=metadata)]
    )
    chunks.extend(doc for doc in split_docs if doc.page_content.strip())


def chunk_documents(pages: list[Document]) -> list[Document]:
    """Turn per-page documents into structure-aware retrieval chunks."""
    if not pages:
        return []

    toc_pages = [p.page_content for p in pages if _looks_like_toc(p.page_content)]
    content_pages = [p.page_content for p in pages if not _looks_like_toc(p.page_content)]

    chunks: list[Document] = []
    toc_titles: set[str] = set()

    if toc_pages:
        toc_full = clean_text("\n\n".join(toc_pages))
        toc_titles = _extract_toc_titles(toc_full)
        chunks.append(
            Document(
                page_content=toc_full,
                metadata={"content_type": "toc", "parent_section": ""},
            )
        )

    # Split on detected headings; fall back to a single block (recursively split
    # downstream) when a document has no detectable structure.
    if content_pages:
        content_full = clean_text("\n\n".join(content_pages))
        sections = _split_into_sections(content_full, toc_titles)
        if not sections:
            sections = [("", content_full)]

        for heading, text in _merge_tiny_sections(sections):
            _add_section_chunks(text, chunks, "content", heading[:120])

    return chunks
