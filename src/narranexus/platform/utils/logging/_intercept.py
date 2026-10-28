"""
@file_name: _intercept.py
@author: Bin Liang
@date: 2026-04-28
@description: InterceptHandler — bridge stdlib logging to loguru.

Adapted from the official loguru README. Routes uvicorn / httpx / mcp /
fastapi log records into our loguru sinks so operators see one format
and one set of files. A small allowlist of high-volume loggers is
clamped to WARNING by default; raise via NEXUS_LOG_LEVEL or call
``logging.getLogger(name).setLevel(...)`` after setup if needed.
"""
from __future__ import annotations

import inspect
import logging

from loguru import logger


class InterceptHandler(logging.Handler):
    """Forward each stdlib LogRecord to loguru with the right caller frame."""

    def emit(self, record: logging.LogRecord) -> None:
        # Map stdlib level name to a loguru level if one exists; else use
        # the numeric value. loguru allows numeric levels directly.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk back to the frame that actually called logging — skip
        # frames inside the logging module and importlib bootstrap so
        # the resulting loguru record points at user code, not the
        # bridge plumbing.
        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# Loggers we explicitly clamp so their INFO/DEBUG noise does not drown
# out our own INFO line. Operators wanting the chatter can flip these
# back per-process via env or stdlib API.
_NOISY_LOGGERS_WARN: tuple[str, ...] = (
    "uvicorn.access",
    "httpx",
    "httpcore",
    "mcp",
    "asyncio",
)


def install_intercept_handler() -> None:
    """Install the bridge on the root stdlib logger.

    ``force=True`` clears handlers added by anything that called
    ``logging.basicConfig`` before us (uvicorn does this), guaranteeing
    we are the only handler on root.
    """
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in _NOISY_LOGGERS_WARN:
        logging.getLogger(name).setLevel(logging.WARNING)
