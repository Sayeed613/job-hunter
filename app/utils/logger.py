"""Structured logging utilities for Project Headhunter."""

from __future__ import annotations

import logging
import sys
from typing import Any


def setup_logger(level: str = "INFO") -> logging.Logger:
    """Configure and return the root application logger.

    The logger writes structured log lines to ``stdout``.  Calling this
    function more than once is safe — duplicate handlers are not added.

    Args:
        level: Logging level string (e.g. ``"DEBUG"``, ``"INFO"``,
            ``"WARNING"``).

    Returns:
        Configured ``headhunter`` logger instance.
    """
    logger = logging.getLogger("headhunter")
    logger.setLevel(level.upper())

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level.upper())
    handler.setFormatter(_StructuredFormatter())
    logger.addHandler(handler)

    return logger


class _StructuredFormatter(logging.Formatter):
    """Produces human-readable log lines with optional key=value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        extra: dict[str, Any] = getattr(record, "extra", None) or {}
        extra_str = ""
        if extra:
            pairs = " ".join(f"{k}={v!r}" for k, v in extra.items())
            extra_str = f"  [{pairs}]"

        return (
            f"[{self.formatTime(record, '%Y-%m-%d %H:%M:%S')}]"
            f" {record.levelname:7s}"
            f"  {record.getMessage()}"
            f"{extra_str}"
        )
