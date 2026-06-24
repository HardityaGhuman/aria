"""Tests for structure-aware chunking: overview tagging + markdown list safety."""
# pyrefly: ignore [missing-import]
from langchain_core.documents import Document

from backend.rag.chunking import chunk_documents

_LONG = "Concrete policy sentence with real payload. " * 8  # > 250 chars


def _doc(text: str) -> list[Document]:
    return [Document(page_content=text, metadata={})]


def test_h1_title_block_tagged_overview():
    md = (
        "# Doc Title\n\n"
        "This document explains the policy in general terms and lists the topics it "
        "covers. It applies to all employees and points to related documents for the "
        "specifics. This is exactly the low-signal overview text that enumerates "
        "topics and so matches many queries semantically, yet answers nothing on its "
        "own, which is why it should not win retrieval slots over real sections.\n\n"
        f"## 1. Real Section\n\n{_LONG}"
    )
    chunks = chunk_documents(_doc(md))
    overview = [c for c in chunks if c.metadata["content_type"] == "overview"]
    content = [c for c in chunks if c.metadata["content_type"] == "content"]
    assert any("This document explains" in c.page_content for c in overview)
    assert any("Real Section" in c.page_content for c in content)
    # overview text must NOT also appear inside a content chunk
    assert not any("This document explains" in c.page_content for c in content)


def test_numbered_list_items_not_split_as_headings():
    md = (
        "# Title\n\n"
        "Intro paragraph that is long enough to be its own standalone block so the "
        "overview path is exercised and nothing merges across the boundary here, "
        "padding to clear the thin-section threshold cleanly.\n\n"
        "## 2. Steps\n\n"
        f"1. **Verbal warning.** {_LONG}\n"
        f"2. **Written warning.** {_LONG}\n"
        f"3. **Final warning.** {_LONG}"
    )
    chunks = chunk_documents(_doc(md))
    steps = [c.page_content for c in chunks if "Verbal warning" in c.page_content]
    assert steps, "Steps section missing"
    # all three numbered items stay together — section not fragmented at 1./2./3.
    assert "Written warning" in steps[0] and "Final warning" in steps[0]


def test_structureless_doc_not_tagged_overview():
    # A doc with no markdown headings must keep its content retrievable
    # (not silently excluded as overview).
    chunks = chunk_documents(_doc(_LONG))
    assert chunks
    assert all(c.metadata["content_type"] != "overview" for c in chunks)


def test_tabular_rows_pass_through_as_one_chunk_each():
    rows = [
        Document(page_content="[Table: t.csv | L4]\nlevel: L4\nbase_salary_usd: 165000",
                 metadata={"is_tabular": True, "content_type": "reference_table"}),
        Document(page_content="[Table: t.csv | L5]\nlevel: L5\nbase_salary_usd: 205000",
                 metadata={"is_tabular": True, "content_type": "reference_table"}),
    ]
    chunks = chunk_documents(rows)
    assert len(chunks) == 2
    assert all(c.metadata["content_type"] == "reference_table" for c in chunks)
    assert "base_salary_usd: 165000" in chunks[0].page_content
