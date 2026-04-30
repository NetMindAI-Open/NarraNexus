"""
@file_name: _context.py
@author: Bin Liang
@date: 2026-04-28
@description: bind_event — semantic wrapper around logger.contextualize.

Pure forward to loguru. The wrapper exists so call sites read as
``with bind_event(event_id=...)`` rather than the generic
``logger.contextualize``. It also concentrates our convention in one
spot — if we ever need to add validation (e.g. reject unknown keys) or
an audit trail, this is the only place to change.

Scope: contextvars-based, asyncio-task-local. Inner binds shadow outer
keys; outer keys remain visible after the inner block exits.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from loguru import logger


@contextmanager
def bind_event(**kwargs: object) -> Iterator[None]:
    """Bind per-event fields to loguru's contextvar for the with-block.

    Common keys: ``run_id``, ``event_id``, ``trigger_id``, ``agent_id``,
    ``user_id``. Any string-like value is accepted; loguru renders them
    via ``{extra[key]}`` in the format template.

    Use at: AgentRuntime.run() entry, each trigger entry, HTTP middleware.
    """
    with logger.contextualize(**kwargs):
        yield
