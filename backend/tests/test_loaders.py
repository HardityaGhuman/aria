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


def test_csv_row_becomes_labeled_chunk(tmp_path):
    csv_path = os.path.join(tmp_path, "salary-bands.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("level,title,region,base_salary_usd\n")
        f.write("L4,Senior Engineer,US,165000\n")
        f.write("L5,Staff Engineer,US,205000\n")
    with open(csv_path + ".meta.yaml", "w", encoding="utf-8") as f:
        f.write("department: finance\naccess_tier: hr_only\nregion: global\n"
                "doc_type: reference_table\ntitle: Salary Bands\n")
    docs = load_document(csv_path, department_fallback="finance")
    assert len(docs) == 2
    first = docs[0]
    assert first.metadata["access_tier"] == "hr_only"
    assert first.metadata["is_tabular"] is True
    assert first.metadata["content_type"] == "reference_table"
    assert "level: L4" in first.page_content
    assert "base_salary_usd: 165000" in first.page_content
    assert first.page_content.startswith("[Table: salary-bands.csv")
