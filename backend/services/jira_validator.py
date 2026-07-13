"""services/jira_validator.py
--------------------------
Deterministic pre-draft gate. No LLM anywhere: allowlist membership + length checks
on the extracted fields, so the same request always yields the same verdict and is
fully unit-testable. Unlike leave there is no external "balance" analogue — every check
is pure/local. Checks run in a fixed order and short-circuit on the first failure with
a short, user-safe reason. Fail-closed: an unknown project is rejected here, before any
approval is requested."""
from dataclasses import dataclass

from backend.core.config import (
    JIRA_ALLOWED_ISSUE_TYPES,
    JIRA_ALLOWED_PROJECTS,
    JIRA_MAX_DESCRIPTION_LEN,
    JIRA_MAX_SUMMARY_LEN,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None


def validate_jira(fields: dict) -> ValidationResult:
    project = (fields.get("project") or "").strip()
    issue_type = (fields.get("issue_type") or "").strip()
    summary = (fields.get("summary") or "").strip()
    description = fields.get("description") or ""

    if project not in JIRA_ALLOWED_PROJECTS:
        return ValidationResult(False, "That project is not one I can raise requests for.")
    if issue_type not in JIRA_ALLOWED_ISSUE_TYPES:
        return ValidationResult(False, "That request type is not recognised.")
    if not summary:
        return ValidationResult(False, "The request needs a short summary.")
    if len(summary) > JIRA_MAX_SUMMARY_LEN:
        return ValidationResult(False, f"The summary is too long (max {JIRA_MAX_SUMMARY_LEN}).")
    if len(description) > JIRA_MAX_DESCRIPTION_LEN:
        return ValidationResult(False, f"The description is too long (max {JIRA_MAX_DESCRIPTION_LEN}).")
    return ValidationResult(True, None)
