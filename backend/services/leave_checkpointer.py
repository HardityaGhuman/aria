"""services/leave_checkpointer.py
-------------------------------
One process-wide LangGraph Postgres checkpointer. The Case's durable resume state
lives here (distinct from our leave_cases projection). Opened once at startup with
``.setup()`` (idempotent table creation), reused for every Case, closed at shutdown.
Kept separate from core/db.py's app pool because it is framework-owned state."""
from backend.core.config import LANGGRAPH_CHECKPOINT_DSN

_checkpointer = None
_cm = None


def get_checkpointer():
    """Open (once) and return the process-wide PostgresSaver."""
    global _checkpointer, _cm
    if _checkpointer is None:
        from langgraph.checkpoint.postgres import PostgresSaver
        _cm = PostgresSaver.from_conn_string(LANGGRAPH_CHECKPOINT_DSN)
        _checkpointer = _cm.__enter__()
        _checkpointer.setup()  # idempotent: creates checkpoint tables/indexes
    return _checkpointer


def close_checkpointer() -> None:
    global _checkpointer, _cm
    if _cm is not None:
        _cm.__exit__(None, None, None)
        _checkpointer = None
        _cm = None
