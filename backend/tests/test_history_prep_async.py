"""Event-loop safety (tighten_plan 3.6).

`_prepare_history` does synchronous DB reads and can trigger an LLM summarize
call. It was invoked bare inside the async chat handlers, blocking the event loop
for the whole request (starving every other in-flight request). It must run in a
worker thread. `_prepare_history_async` is the seam that guarantees that.
"""
import asyncio
import threading

# The history-prep seam moved into the shared read pipeline (§6); the transports
# call it from there. Aliased so the test body reads unchanged.
from backend.services import read_pipeline as chat_service


def test_prepare_history_runs_off_the_event_loop(monkeypatch):
    main_thread = threading.current_thread()
    seen = {}

    def fake_prepare(session_id):
        seen["thread"] = threading.current_thread()
        seen["session_id"] = session_id
        return [{"role": "user", "content": "hi"}]

    monkeypatch.setattr(chat_service, "_prepare_history", fake_prepare)

    result = asyncio.run(chat_service._prepare_history_async("s-1"))

    assert result == [{"role": "user", "content": "hi"}]
    assert seen["session_id"] == "s-1"
    # The blocking work ran in a worker thread, not the event-loop thread.
    assert seen["thread"] is not main_thread
