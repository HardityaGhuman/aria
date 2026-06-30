"""
core/logging.py
---------------
Standardised application logging.

Call ``setup_logging()`` once at startup, and use ``get_logger(__name__)`` in
modules instead of ``print()``. ``get_logger`` configures logging on first use,
so library modules can log safely even if the app forgot to set it up.
"""
import logging
import sys

_CONFIGURED = False


class _DropOptionsPreflight(logging.Filter):
    """Drop uvicorn access lines for CORS preflight (OPTIONS) requests.

    The browser fires an OPTIONS before every credentialed call, so each real
    request shows up twice in the access log. The preflight 200 carries no
    diagnostic value — only the real method/path does."""

    def filter(self, record: logging.LogRecord) -> bool:
        return '"OPTIONS ' not in record.getMessage()


def _quiet_noisy_libraries() -> None:
    """Silence third-party log spam that drowns out our own telemetry.

    - LiteLLM logs every completion at INFO ("LiteLLM completion() model=...",
      "Wrapper: Completed Call") through its OWN handler, then the record also
      propagates to our root handler — so each line prints twice. Raising its
      level to WARNING kills the INFO chatter at the source (both copies).
    - litellm also raw-prints a "Provider List: https://..." banner; that is
      gated by ``suppress_debug_info``.
    - uvicorn.access doubles every request with a CORS OPTIONS preflight line.
    """
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").addFilter(_DropOptionsPreflight())
    try:
        import litellm

        litellm.suppress_debug_info = True
        litellm.set_verbose = False
    except Exception:
        pass  # litellm not importable here is fine; quieting is best-effort.


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    _quiet_noisy_libraries()
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
