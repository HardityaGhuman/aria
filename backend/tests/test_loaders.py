import os
from backend.rag.loaders import load_document

def _write(tmp_path, name, text):
    p = os.path.join(tmp_path, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p

def test_frontmatter_axes_are_stamped(tmp_path):
    path = _write(tmp_path, "doc.md", (
        "---\n"
        "department: finance\n"
        "access_tier: hr_only\n"
        "region: india\n"
        "doc_type: policy\n"
        "version: 2025.1\n"
        "effective_date: 2025-01-01\n"
        "title: X\n"
        "---\n\n## Body\nText with 12% EPF.\n"
    ))
    doc = load_document(path)[0]
    assert doc.metadata["region"] == "india"
    assert doc.metadata["doc_type"] == "policy"
    assert doc.metadata["version"] == "2025.1"
    assert doc.metadata["effective_date"] == "2025-01-01"

def test_axes_default_when_absent(tmp_path):
    path = _write(tmp_path, "doc.md", "## Body\nNo frontmatter here.\n")
    doc = load_document(path, department_fallback="hr")[0]
    assert doc.metadata["department"] == "hr"
    assert doc.metadata["region"] == "global"
    assert doc.metadata["doc_type"] == "policy"
    assert doc.metadata["version"] == "2026.1"
    assert doc.metadata["effective_date"] == "2026-01-01"
