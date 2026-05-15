"""
@file_name: channel_debounce_merger.py
@date: 2026-05-08
@description: Merge rapid-fire IM messages from the same sender into one.

When a user sends 3 messages within a few hundred ms (very common in
chat apps — "hi", "are you there", "i wanted to ask about X"), running
the agent 3 separate times wastes tokens and produces incoherent
half-replies. This merger debounces N messages from the same
``(chat_id, sender_id)`` key into a single combined ``ParsedMessage``
flushed after a quiet window.

Inspired by OpenClaw's debounce pattern. NarraNexus does not have this
capability today — Lark's trigger pipes every event straight through
to a worker.

Public surface:

    submit(message, flush_callback)
        Buffer ``message`` under its (chat_id, sender_id) key. If no
        further submit lands within ``window_ms``, flush via the
        callback. New submits cancel and rearm the timer.

    flush_all()
        Cancel all timers and synchronously flush every pending
        buffer. Used by ``ChannelTriggerBase.stop`` so a graceful
        shutdown does not lose buffered messages.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Awaitable, Callable

from loguru import logger

from xyz_agent_context.schema.parsed_message import ParsedMessage


FlushCallback = Callable[[ParsedMessage], Awaitable[None]]


class ChannelDebounceMerger:
    """Group rapid-fire messages by (chat_id, sender_id) and flush merged."""

    def __init__(self, window_ms: int = 2000):
        if window_ms <= 0:
            raise ValueError("window_ms must be positive")
        self._window_ms = window_ms
        self._pending: dict[str, list[ParsedMessage]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(msg: ParsedMessage) -> str:
        """Group key: (chat_id, sender_id)."""
        return f"{msg.chat_id}::{msg.sender_id}"

    @property
    def window_ms(self) -> int:
        return self._window_ms

    async def submit(self, msg: ParsedMessage, flush_callback: FlushCallback) -> None:
        """
        Buffer ``msg`` and schedule a flush at ``now + window_ms``.

        Subsequent submits with the same key cancel the pending timer
        and rearm it — only after a quiet window does flush_callback
        fire with the merged result.
        """
        k = self._key(msg)
        loop = asyncio.get_running_loop()
        async with self._lock:
            existing_timer = self._timers.get(k)
            if existing_timer is not None:
                existing_timer.cancel()
            self._pending.setdefault(k, []).append(msg)
            self._timers[k] = loop.call_later(
                self._window_ms / 1000.0,
                lambda key=k: asyncio.ensure_future(self._flush(key, flush_callback)),
            )

    async def flush_all(self, flush_callback: FlushCallback) -> None:
        """Synchronously flush every pending buffer. Cancels timers."""
        async with self._lock:
            keys = list(self._pending.keys())
            for k in keys:
                t = self._timers.pop(k, None)
                if t is not None:
                    t.cancel()
        for k in keys:
            await self._flush(k, flush_callback)

    async def _flush(self, key: str, callback: FlushCallback) -> None:
        async with self._lock:
            messages = self._pending.pop(key, [])
            self._timers.pop(key, None)
        if not messages:
            return
        merged = self._merge(messages)
        try:
            await callback(merged)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"ChannelDebounceMerger flush callback raised for key={key!r}: "
                f"{type(e).__name__}: {e}"
            )

    @staticmethod
    def _merge(messages: list[ParsedMessage]) -> ParsedMessage:
        """
        Combine N messages into one.

        - Last message wins on metadata (timestamp, message_id) — newest
          view of the conversation.
        - Bodies join with newlines, in arrival order, skipping empties.
        - Media URLs concatenate.

        Returns a NEW ParsedMessage. Never mutates the inputs — earlier
        merger versions mutated ``latest`` in place, which leaked into
        dedup-store entries and (under a shutdown timer/flush_all race)
        could cause the same merged payload to be dispatched twice.
        """
        if len(messages) == 1:
            return messages[0]
        latest = messages[-1]
        bodies = [m.content for m in messages if m.content]
        return replace(
            latest,
            content="\n".join(bodies) if bodies else "",
            media_urls=[u for m in messages for u in m.media_urls],
        )
