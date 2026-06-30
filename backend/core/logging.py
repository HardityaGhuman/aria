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


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
