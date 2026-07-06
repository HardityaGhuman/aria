"""Atomic session-ownership regression (tighten_plan 3.3).

The route-level owner check (`_require_owned_or_new_session`) and the message
insert (`append_exchange`) run in separate transactions, so two users racing the
same brand-new, client-supplied session_id could both pass the check and the
second write could land in the first's session. The fix claims/verifies the owner
in the SAME transaction as the insert and rejects a mismatch, rolling the whole
exchange back so no cross-user message is ever inserted.
"""
import uuid

import pytest

from backend.core import chat_memory


def _db_or_skip():
    try:
        chat_memory.initialize_chat_memory()
    except chat_memory.ChatMemoryError:
        pytest.skip("Postgres not available for chat-memory tests")


def _new_sid() -> str:
    return "sess-" + uuid.uuid4().hex


def test_second_owner_cannot_write_into_claimed_session():
    _db_or_skip()
    sid = _new_sid()
    chat_memory.append_exchange(sid, "hi from A", "reply to A", owner_user_id=1)

    with pytest.raises(chat_memory.ChatMemoryError):
        chat_memory.append_exchange(sid, "hi from B", "reply to B", owner_user_id=2)

    # B's turn never landed in A's session.
    contents = [m["content"] for m in chat_memory.get_history(sid)]
    assert "hi from B" not in contents
    assert "reply to B" not in contents
    assert contents == ["hi from A", "reply to A"]
    chat_memory.clear_history(sid)


def test_same_owner_can_append_again():
    _db_or_skip()
    sid = _new_sid()
    chat_memory.append_exchange(sid, "q1", "a1", owner_user_id=1)
    chat_memory.append_exchange(sid, "q2", "a2", owner_user_id=1)  # same owner, fine
    contents = [m["content"] for m in chat_memory.get_history(sid)]
    assert contents == ["q1", "a1", "q2", "a2"]
    chat_memory.clear_history(sid)


def test_concurrent_new_session_claim_has_one_winner():
    """Two users race the same brand-new session_id. Exactly one exchange lands;
    the other is rejected and leaves no trace."""
    _db_or_skip()
    import concurrent.futures

    sid = _new_sid()

    def attempt(uid: int) -> bool:
        try:
            chat_memory.append_exchange(sid, f"msg-{uid}", f"reply-{uid}", owner_user_id=uid)
            return True
        except chat_memory.ChatMemoryError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [10, 20]))

    assert results.count(True) == 1  # exactly one winner
    history = chat_memory.get_history(sid)
    # Only the winner's two turns exist; no interleaving from the loser.
    assert len(history) == 2
    chat_memory.clear_history(sid)
