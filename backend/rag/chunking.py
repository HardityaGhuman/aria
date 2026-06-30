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
CHUNK_VERSION = "2026-06-30-h2-only-split-v11"
# Sections shorter than this are treated as heading/intro blurbs ("In this
# section we explain...") and folded forward into the next real section, so they
# don't survive as low-content chunks that win retrieval slots without payload.
THIN_SECTION_CHARS = 250

SECTION_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)

# Structured heading prefixes for non-markdown (PDF) docs: Roman numerals (IV.),
# decimal (1. / 2.3), lettered (A.). Only used when a doc has no markdown headings.
_HEADING_PREFIX = re.compile(r"^(?:[IVXLCDM]+\.|\d+(?:\.\d+)*\.?|[A-Z]\.)\s+\S")


def _heading_level(line: str) -> int:
    """Markdown ATX heading level (1-6), or 0 if the line is not a heading."""
    match = re.match(r"^(#{1,6})\s+\S", line.strip())
    return len(match.group(1)) if match else 0


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


def _is_heading(line: str, toc_titles: set[str], md_mode: bool) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    # Markdown docs: ONLY H2 (##) is a section boundary. The H1 title stays in
    # the leading block (tagged "overview"), numbered list items ("1. ...") are
    # never headings, and H3+ (### subsections) stay welded to their parent H2.
    # H3-splitting shattered tightly-coupled enumerations — e.g. a "## 4-Stage
    # Interview Loop" section whose four "### Stage N" subparts became separate
    # sibling chunks, so a "what are the stages" query retrieved some siblings
    # but not all and TOP_K cut the rest. Keeping the H2 whole makes the whole
    # enumeration one retrievable unit. (No corpus doc uses H3 without an H2.)
    if md_mode:
        return _heading_level(stripped) == 2
    # Non-markdown (PDF) docs: fall back to TOC titles + structured prefixes
    # (Roman/decimal/lettered), which is where the numbered-prefix heuristic
    # legitimately applies.
    if stripped.lower() in toc_titles:
        return True
    return bool(_HEADING_PREFIX.match(stripped))


def _split_into_sections(content: str, toc_titles: set[str], md_mode: bool) -> list[tuple[str, str]]:
    """Split body text at detected headings into (heading, text) pairs."""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    for line in content.split("\n"):
        if _is_heading(line, toc_titles, md_mode):
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

    # Tabular sources are pre-chunked one-row-per-Document by the loader; the
    # structure-aware path (TOC/heading splitting, tiny-section merge) would
    # corrupt them, so pass them through verbatim as reference_table chunks.
    if pages[0].metadata.get("is_tabular"):
        return [
            Document(
                page_content=p.page_content,
                metadata={**p.metadata, "content_type": "reference_table", "parent_section": ""},
            )
            for p in pages
            if p.page_content.strip()
        ]

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
        md_mode = any(_heading_level(line) >= 1 for line in content_full.split("\n"))
        sections = _split_into_sections(content_full, toc_titles, md_mode)
        if not sections:
            sections = [("", content_full)]

        merged = _merge_tiny_sections(sections)
        for index, (heading, text) in enumerate(merged):
            # In a markdown doc the leading block (H1 title + intro, before the
            # first ## section) carries no answer payload — it just enumerates the
            # doc's topics, so it wins retrieval slots semantically while answering
            # nothing. Tag it "overview" so retrieval can exclude it, the same way
            # TOC pages are excluded. Only when real sections follow; a doc that is
            # ONE block stays "content" so its content is never silently dropped.
            is_overview = md_mode and index == 0 and heading == "" and len(merged) > 1
            content_type = "overview" if is_overview else "content"
            _add_section_chunks(text, chunks, content_type, heading[:120])

    return chunks
