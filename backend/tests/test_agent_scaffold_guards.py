"""Cross-cutting guards for the agentic scaffold: the package re-exports, the
flag-off regression (the live chat path never touches the loop while
AGENT_TOOLS_ENABLED is false), and the no-new-langchain-coupling invariant."""
import ast
import pathlib

import backend.core.config as config


def test_package_reexports():
    from backend.core.tools import (
        Principal, principal_from_user, Tool, ToolResult, ToolRegistry,
        validate_args, role_allows,
    )
    assert Principal and principal_from_user and Tool and ToolResult
    assert ToolRegistry and validate_args and role_allows


def test_flag_is_off_so_pipeline_is_unchanged():
    # The behavioral regression (loop wired into chat) lands in Unit 2; here we
    # assert the master switch is off by default — the guarantee everything rests on.
    assert config.AGENT_TOOLS_ENABLED is False


def test_agent_modules_do_not_import_langchain():
    # The loop + registry + principal must stay free of langchain/langgraph so the
    # decoupling the observability spec bought is not silently undone.
    roots = [
        pathlib.Path("backend/core/tools/principal.py"),
        pathlib.Path("backend/core/tools/base.py"),
        pathlib.Path("backend/core/tools/registry.py"),
        pathlib.Path("backend/core/tools/stub.py"),
        pathlib.Path("backend/services/agent_loop.py"),
    ]
    for path in roots:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None) or ""
                names = [a.name for a in getattr(node, "names", [])]
                blob = mod + " " + " ".join(names)
                assert "langchain" not in blob and "langgraph" not in blob, f"{path} imports {blob}"
