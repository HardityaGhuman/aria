"""The catalog is PURE PYTHON and that is the security property: no model output
ever becomes a tool name. The LLM may only produce KEYS INTO these dicts; anything
else dies in the validator (Task 6)."""
import pytest

from backend.core.access.catalog import ROLE_BUNDLES, TOOL_CATALOG, resolve_tools


def test_every_bundled_tool_is_in_the_catalog():
    for role, tools in ROLE_BUNDLES.items():
        for tool in tools:
            assert tool in TOOL_CATALOG, f"{role} bundles off-catalog tool {tool!r}"


def test_resolve_is_bundle_union_extras_sorted_and_deduped():
    assert resolve_tools("designer", ["slack", "jira"]) == ["figma", "jira", "slack"]


def test_resolve_with_no_extras_is_the_bundle():
    assert resolve_tools("analyst", []) == sorted(ROLE_BUNDLES["analyst"])


def test_resolve_unknown_role_raises():
    with pytest.raises(KeyError):
        resolve_tools("astronaut", [])
