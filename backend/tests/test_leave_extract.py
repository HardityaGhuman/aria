import pytest
from backend.services.leave_extract import extract_leave_fields, LeaveExtractError


def test_extract_parses_valid_json():
    def fake_llm(raw_text):
        return {"start_date": "2026-08-12", "end_date": "2026-08-14", "reason": "vacation"}
    out = extract_leave_fields("Aug 12-14 vacation", llm_call=fake_llm)
    assert out == {"start_date": "2026-08-12", "end_date": "2026-08-14", "reason": "vacation"}


def test_extract_rejects_non_iso_dates():
    def fake_llm(raw_text):
        return {"start_date": "August 12th", "end_date": "2026-08-14", "reason": "x"}
    with pytest.raises(LeaveExtractError):
        extract_leave_fields("bad", llm_call=fake_llm)


def test_extract_rejects_missing_field():
    def fake_llm(raw_text):
        return {"start_date": "2026-08-12"}
    with pytest.raises(LeaveExtractError):
        extract_leave_fields("bad", llm_call=fake_llm)
