"""
@file_name: cancellation.py
@author: Bin Liang
@date: 2026-03-24
@description: Cooperative cancellation token for the agent execution pipeline

Provides a lightweight, thread-safe cancellation mechanism that propagates
through the entire execution stack without relying on asyncio.CancelledError.

Design rationale:
    Using CancellationToken (instead of raw Task.cancel()) gives explicit control
    over WHERE cancellation is checked, enables graceful cleanup at each layer,
    and makes cancellation a first-class concern in the architecture.

Usage:
    token = CancellationToken()

    # Pass token through the pipeline
    async for msg in runtime.run(..., cancellation=token):
        await websocket.send_json(msg)

    # Cancel from another coroutine (e.g., WebSocket stop handler)
    token.cancel()

    # Check in any layer
    if token.is_cancelled:
        return  # Exit gracefully
"""

from __future__ import annotations

import asyncio
from loguru import logger


class CancellationToken:
    """
    Cooperative cancellation token for the agent execution pipeline.

    Thread-safe and async-compatible. Any layer holding this token can:
    - Check `is_cancelled` before starting expensive work
    - Call `raise_if_cancelled()` at natural checkpoints
    - Use `cancel()` to signal all holders to stop

    The token carries a human-readable `reason` for logging/debugging.
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str = ""

    def cancel(self, reason: str = "User requested stop") -> None:
        """Signal cancellation. Idempotent — safe to call multiple times."""
        if not self._event.is_set():
            self._reason = reason
            self._event.set()
            logger.info(f"CancellationToken triggered: {reason}")

    @property
    def is_cancelled(self) -> bool:
        """Non-blocking check. Use at natural checkpoints."""
        return self._event.is_set()

    @property
    def reason(self) -> str:
        """Why cancellation was requested (empty string if not cancelled)."""
        return self._reason

    def raise_if_cancelled(self) -> None:
        """Raise CancelledByUser if the token has been cancelled.

        Use at the start of expensive operations to fail fast.
        """
        if self._event.is_set():
            raise CancelledByUser(self._reason)


class CancelledByUser(Exception):
    """Raised when the user explicitly cancels an agent run.

    Distinct from asyncio.CancelledError so that generic try/except
    blocks don't accidentally swallow it.
    """

    def __init__(self, reason: str = "User requested stop"):
        self.reason = reason
        super().__init__(reason)
