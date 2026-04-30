"""
@file_name: _timing.py
@author: Bin Liang
@date: 2026-04-28
@description: timed — measure elapsed time and emit a structured TIMED log.

Doubles as decorator and context manager. Async-aware: inspects the
wrapped callable and returns either a sync or coroutine wrapper so the
caller never has to choose.

Behavior:
  - Success: ``logger.log(level, "[TIMED] <name> ok elapsed_ms=...")``
  - Slow (above slow_threshold_ms): same line escalated to WARNING.
  - Exception: ``logger.exception(...)`` records the failure with full
    stack, then re-raises so semantics match the un-timed code path.

Each ``with timed(...)`` / ``@timed(...)`` invocation gets its own
clock — re-using a single returned object across nested or concurrent
scopes is safe because ``__enter__`` always resets the start time.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable

from loguru import logger


_DEFAULT_LEVEL = "INFO"
_SLOW_LEVEL = "WARNING"


class _Timed:
    """Re-entrant context manager + decorator. Holds the configuration;
    a fresh start time is captured on every ``__enter__``."""

    __slots__ = ("name", "level", "slow_threshold_ms", "_start")

    def __init__(
        self,
        name: str,
        *,
        level: str,
        slow_threshold_ms: int | None,
    ) -> None:
        self.name = name
        self.level = level
        self.slow_threshold_ms = slow_threshold_ms
        self._start: float | None = None

    def __enter__(self) -> "_Timed":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = (time.monotonic() - (self._start or time.monotonic())) * 1000.0
        if exc_type is not None:
            logger.exception(
                "[TIMED] {name} failed elapsed_ms={ms:.1f}",
                name=self.name,
                ms=elapsed_ms,
            )
            return False  # propagate
        level = self.level
        if (
            self.slow_threshold_ms is not None
            and elapsed_ms >= self.slow_threshold_ms
        ):
            level = _SLOW_LEVEL
        logger.log(
            level,
            "[TIMED] {name} ok elapsed_ms={ms:.1f}",
            name=self.name,
            ms=elapsed_ms,
        )
        return False

    def __call__(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        # New _Timed per call so concurrent invocations of the wrapped
        # function never share a clock.
        cfg = (self.name, self.level, self.slow_threshold_ms)

        if inspect.isasyncgenfunction(fn):
            @functools.wraps(fn)
            async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _Timed(cfg[0], level=cfg[1], slow_threshold_ms=cfg[2]):
                    async for item in fn(*args, **kwargs):
                        yield item

            return asyncgen_wrapper

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _Timed(cfg[0], level=cfg[1], slow_threshold_ms=cfg[2]):
                    return await fn(*args, **kwargs)

            return async_wrapper

        if inspect.isgeneratorfunction(fn):
            @functools.wraps(fn)
            def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                with _Timed(cfg[0], level=cfg[1], slow_threshold_ms=cfg[2]):
                    yield from fn(*args, **kwargs)

            return gen_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with _Timed(cfg[0], level=cfg[1], slow_threshold_ms=cfg[2]):
                return fn(*args, **kwargs)

        return sync_wrapper


def timed(
    name: str,
    *,
    level: str = _DEFAULT_LEVEL,
    slow_threshold_ms: int | None = None,
) -> _Timed:
    """Measure elapsed time. Use as decorator or context manager.

    Examples
    --------
    >>> @timed("step_1.select_narrative")
    ... async def step_1(...): ...

    >>> with timed("mcp.call_tool", slow_threshold_ms=2000):
    ...     await client.call_tool(...)
    """
    return _Timed(name, level=level, slow_threshold_ms=slow_threshold_ms)
