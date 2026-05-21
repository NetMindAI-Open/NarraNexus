"""
@file_name: test_ws_exception_filter.py
@author: Bin Liang
@date: 2026-05-19
@description: `_ws_loop_exception_filter` is the per-thread asyncio loop's
exception handler for the Lark SDK WebSocket client.

Two-shape responsibility:

1. **For known transient connection-class exceptions** (``ConnectionResetError``
   / ``OSError`` / any ``websockets.*`` exception) raised by the SDK's
   fire-and-forget ``_receive_message_loop`` task on a WS disconnect: the
   filter **must call ``loop.stop()``** so the per-thread event loop running
   ``ws_client.start()`` terminates. That makes the daemon thread die, which
   is the signal the outer ``while t.is_alive() and self.running`` poll in
   ``_subscribe_loop`` uses to drive backoff + reconnect. We must not pass
   the exception to the default handler — that would just log noise without
   stopping the loop, which is the original 2026-05-18 zombie bug. (Default
   handler is also skipped to keep logs clean.)

2. **For unknown / unrelated exceptions** (e.g. a ``ValueError`` from
   application code) and for contexts with no exception (e.g. slow-callback
   warnings): the filter **must pass through to ``default_exception_handler``
   and must NOT call ``loop.stop()``**, because unrelated bugs deserve a
   loud trace, not a silent loop termination.

Observed incident that motivates this (2026-05-18 EC2):
  - 19:16–19:24 UTC: 10+ WS connections die with `keepalive ping timeout`
    / `Connection reset by peer`.
  - 19:24 → 05:19 UTC (next day): 11 hours of total silence. Zero rows in
    ``lark_trigger_audit`` for the affected agents. Zero reconnect attempts.
  - Root cause: the SDK's ``start()`` blocks on a forever-sleeping
    ``_select()`` coroutine; the WS receive task is fire-and-forget and its
    raised exceptions land in this handler. The previous version of this
    filter swallowed those exceptions, leaving the loop running and the
    daemon thread alive → outer reconnect loop never triggered → zombie.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import (
    _ws_loop_exception_filter,
)


class _FakeLoop:
    """Stand-in for an asyncio loop. Records default-handler + stop() calls."""

    def __init__(self):
        self.default_handler_called_with: list[dict] = []
        self.stop_called: bool = False

    def default_exception_handler(self, context):
        self.default_handler_called_with.append(context)

    def stop(self):
        self.stop_called = True


# ──────────────────────────────────────────────────────────────────────────
# Unit tests — connection-class exceptions stop the loop, don't log noise
# ──────────────────────────────────────────────────────────────────────────


def test_connection_reset_error_stops_loop_without_logging():
    loop = _FakeLoop()
    ctx = {"exception": ConnectionResetError(104, "Connection reset by peer")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.stop_called is True
    assert loop.default_handler_called_with == []


def test_os_error_stops_loop_without_logging():
    loop = _FakeLoop()
    ctx = {"exception": OSError("broken pipe")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.stop_called is True
    assert loop.default_handler_called_with == []


def test_websockets_module_exception_stops_loop_without_logging():
    """Hand-built type from the ``websockets.*`` namespace; we match by module
    prefix so we don't take a hard dependency on the SDK's specific exception
    classes."""
    loop = _FakeLoop()

    class _FakeWsExc(Exception):
        pass

    _FakeWsExc.__module__ = "websockets.exceptions"
    ctx = {"exception": _FakeWsExc("keepalive ping timeout")}

    _ws_loop_exception_filter(loop, ctx)
    assert loop.stop_called is True
    assert loop.default_handler_called_with == []


# ──────────────────────────────────────────────────────────────────────────
# Unit tests — unknown / unrelated exceptions are passed through, loop runs
# ──────────────────────────────────────────────────────────────────────────


def test_unknown_exception_passes_through_without_stopping_loop():
    loop = _FakeLoop()
    ctx = {"exception": ValueError("unrelated bug")}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.stop_called is False
    assert loop.default_handler_called_with == [ctx]


def test_context_without_exception_passes_through_without_stopping_loop():
    """Loops occasionally call the handler with no exception (e.g. slow-callback
    warnings). Don't swallow and don't stop the loop."""
    loop = _FakeLoop()
    ctx = {"message": "Slow callback detected"}
    _ws_loop_exception_filter(loop, ctx)
    assert loop.stop_called is False
    assert loop.default_handler_called_with == [ctx]


# ──────────────────────────────────────────────────────────────────────────
# Integration test — real loop running an SDK-style _select() blocker
# terminates promptly when a fire-and-forget task raises a connection error
# ──────────────────────────────────────────────────────────────────────────


def test_real_loop_terminates_when_fire_and_forget_task_raises_connection_error():
    """End-to-end shape of the 2026-05-18 zombie incident: a per-thread loop is
    blocked on a forever-sleep coroutine (mirroring the SDK's ``_select()``).
    A fire-and-forget task raises ``ConnectionResetError`` (mirroring the
    SDK's ``_receive_message_loop`` on a real WS disconnect). With the fixed
    filter, the loop must terminate quickly so ``ws_client.start()`` returns
    and the daemon thread dies — letting the outer ``_subscribe_loop``
    reconnect logic take over."""

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(_ws_loop_exception_filter)

    async def select_blocker():
        # SDK's actual _select() is `while True: await asyncio.sleep(3600)`.
        # We use a single long sleep here for the same effect.
        await asyncio.sleep(60)

    async def raise_after_delay():
        await asyncio.sleep(0.05)
        raise ConnectionResetError(104, "Connection reset by peer")

    loop.create_task(raise_after_delay())
    started = time.monotonic()
    try:
        loop.run_until_complete(select_blocker())
    except RuntimeError:
        # Expected: loop.stop() before the future completed.
        pass
    finally:
        # Drain any pending tasks the loop won't run again.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
    elapsed = time.monotonic() - started

    # If the filter merely swallows the exception, ``select_blocker`` keeps
    # sleeping and ``run_until_complete`` blocks for the full 60s. Bound the
    # acceptable wall-clock at 2s to leave headroom for slow CI without
    # masking a regression that would keep the loop alive indefinitely.
    assert elapsed < 2.0, (
        f"loop did not terminate after a connection error fired in a "
        f"fire-and-forget task; ran for {elapsed:.2f}s (this is the "
        f"2026-05-18 zombie bug)"
    )
