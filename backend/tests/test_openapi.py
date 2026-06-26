"""Tests for the frozen OpenAPI contract (Task 8): the schema builds and every
API route declares a real response schema (no bare-dict 200s the client can't type)."""
from backend.main import app

_PREFIXES = ("/auth", "/chat", "/admin", "/me")


def test_openapi_builds():
    schema = app.openapi()
    assert schema["openapi"]
    assert schema["paths"]


def test_every_route_has_a_response_schema():
    schema = app.openapi()
    offenders = []
    for path, operations in schema["paths"].items():
        if not path.startswith(_PREFIXES):
            continue
        for method, op in operations.items():
            # The SSE stream isn't JSON — it's an event stream with no body model.
            if path.endswith("/stream"):
                continue
            for code, resp in op.get("responses", {}).items():
                if not code.startswith("2"):
                    continue
                content = resp.get("content", {})
                model_schema = content.get("application/json", {}).get("schema", {})
                if not model_schema:
                    offenders.append(f"{method.upper()} {path} -> {code}")
    assert not offenders, f"Routes missing a response schema: {offenders}"
