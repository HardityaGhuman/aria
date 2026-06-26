"""Tests for the standardized response + error envelope (Task 2).

A standalone app wires the chat router + error handlers and overrides auth, so
these tests need no Postgres, Chroma, or real token — only the contract shapes.
"""
# pyrefly: ignore [missing-import]
from fastapi import FastAPI
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

import backend.routes.chat as chatroute
from backend.core.auth import get_current_user
from backend.core.errors import AppError, register_error_handlers
from backend.core.ratelimit import limiter
from backend.services.chat_service import ChatResult


def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter  # /chat is rate-limited; slowapi reads app.state
    register_error_handlers(app)
    app.include_router(chatroute.router, prefix="/chat")
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "role": "employee", "region": "us"}

    @app.get("/boom")
    def _boom():
        raise AppError("llm_timeout", "model timed out", status_code=504)

    return app


client = TestClient(_make_app())


def test_validation_error_is_enveloped():
    resp = client.post("/chat", json={})  # missing required `message`
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["detail"]  # carries field-level errors


def test_app_error_renders_uniform_body():
    resp = client.get("/boom")
    assert resp.status_code == 504
    assert resp.json() == {
        "error": {"code": "llm_timeout", "message": "model timed out", "detail": None}
    }


def test_chat_success_uses_envelope(monkeypatch):
    async def _fake_reply(*args, **kwargs):
        return ChatResult(
            reply="You get 20 days of PTO.",
            context_used="[Source: time-and-leave/working-hours-and-pto.md]",
            sources=[{
                "source": "time-and-leave/working-hours-and-pto.md",
                "chunk": 4,
                "department": "time-and-leave",
                "access_tier": "all",
                "section": "PTO",
                "distance": 0.3,
            }],
            status="ok",
        )

    monkeypatch.setattr(chatroute, "generate_chat_reply", _fake_reply)
    resp = client.post("/chat", json={"message": "pto?", "session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "You get 20 days of PTO."
    assert body["status"] == "ok"
    assert body["session_id"] == "s1"
    assert isinstance(body["latency_ms"], int)
    src = body["sources"][0]
    assert src["document_id"] == "time-and-leave/working-hours-and-pto.md"
    assert src["file"] == "working-hours-and-pto.md"
    assert src["section"] == "PTO"
    assert src["source_type"] == "all"
