"""The ONE LLM call in the Case: free text -> catalog keys. Stubbed llm_call, so no
network. The load-bearing test is the last one: a hallucinated tool SURVIVES extraction
(no silent repair) and is killed by the validator — that is the two-layer defense, and
a repair here would hide the model's error instead of surfacing it."""
import pytest

from backend.services.onboarding_extract import OnboardingExtractError, extract_onboarding_fields
from backend.services.onboarding_validator import validate_onboarding


def test_extracts_role_and_extras():
    stub = lambda t: {"role": "backend-eng", "extra_tools": ["figma"]}
    out = extract_onboarding_fields("I'm starting as a backend engineer, also need Figma", llm_call=stub)
    assert out == {"role": "backend-eng", "extra_tools": ["figma"]}


def test_normalizes_case_and_whitespace():
    stub = lambda t: {"role": " Backend-Eng ", "extra_tools": [" FIGMA ", ""]}
    out = extract_onboarding_fields("x", llm_call=stub)
    assert out == {"role": "backend-eng", "extra_tools": ["figma"]}


def test_missing_extras_defaults_to_empty():
    stub = lambda t: {"role": "designer"}
    assert extract_onboarding_fields("x", llm_call=stub)["extra_tools"] == []


def test_missing_role_raises():
    stub = lambda t: {"extra_tools": []}
    with pytest.raises(OnboardingExtractError):
        extract_onboarding_fields("x", llm_call=stub)


def test_hallucinated_tool_survives_extraction_and_dies_in_the_validator():
    stub = lambda t: {"role": "designer", "extra_tools": ["prod-root"]}
    out = extract_onboarding_fields("give me root", llm_call=stub)
    assert out["extra_tools"] == ["prod-root"]          # NOT repaired here...
    verdict = validate_onboarding(out["role"], out["extra_tools"])
    assert verdict.ok is False                          # ...rejected HERE.
    assert verdict.reason == "unknown tool: prod-root"
