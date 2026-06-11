"""
@file_name: broadcaster.py
@author: Bin Liang
@date: 2026-05-13
@description: Per-run in-memory pub/sub for live WebSocket subscribers.

Background
----------
Once agent_loop is detached from the WebSocket task (Phase C of the
agent-runtime-lifecycle spec), some new component has to fan out the
agent's stream of events to every browser tab subscribed to that run.
This module is that component — a tiny in-memory pub/sub local to one
BackgroundRun.

Lifecycle binding (no TTL)
--------------------------
The user is explicit on this point: there is no time-based retention.
A Broadcaster lives exactly as long as its BackgroundRun's task is
alive. When the run reaches a terminal state, BackgroundRun calls
``close()`` and the Broadcaster releases every subscriber. Reconnects
after the run has ended hit the database (event_stream + events table)
for full replay — they do NOT need a stale broadcaster sitting around.

Current-thinking-buffer
-----------------------
Because Phase C only writes a thinking SEGMENT row to event_stream
when the segment ENDS (i.e. when a non-thinking event arrives), a
subscriber that reconnects mid-segment would otherwise see a gap
between "what's in the DB" and "what the live broadcast is emitting".
The Broadcaster therefore carries a ``current_thinking_buffer`` —
the BackgroundRun keeps this in sync with the in-flight segment, and
``subscribe()`` hands new subscribers a snapshot before they join the
live stream.

Concurrency model
-----------------
All state mutations (subscriber add/remove, current_thinking_buffer
update, publish) happen on the same event loop the BackgroundRun
task is bound to. There is no cross-loop access. asyncio.Queue is
the per-subscriber mailbox — bounded to avoid runaway memory if a
specific WS consumer is slow, but the bound is generous and chosen
to dwarf realistic in-flight stream depth.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import AsyncIterator, Optional

from loguru import logger


# Per-subscriber bounded queue. Generous enough that a healthy WebSocket
# consumer never hits the bound, but tight enough that a stuck WS does
# not balloon backend memory. If we hit the bound, the producer drops
# the message FOR THAT SUBSCRIBER only (others are unaffected) and
# emits a warning — this is the only allowed lossy path, and it only
# fires when an individual WS peer is misbehaving.
_PER_SUBSCRIBER_QUEUE_BOUND = 4096


class Subscriber:
    """One WebSocket session subscribed to a run.

    Holds a bounded queue. ``__aiter__`` lets the WebSocket handler
    write ``async for event in subscriber: ws.send_json(event)``.
    """

    __slots__ = ("session_id", "_queue", "_closed")

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._queue: asyncio.Queue[Optional[dict]] = asyncio.Queue(
            maxsize=_PER_SUBSCRIBER_QUEUE_BOUND
        )
        self._closed = False

    def push(self, event: dict) -> None:
        """Append an event to this subscriber's queue. Drops with a
        warning if the queue is full (slow WS consumer).

        Synchronous (put_nowait) by design: the terminal `complete`
        frame is published immediately before ``Broadcaster.close()``
        with no event-loop yield in between. A task-deferred enqueue
        would race the close (which flips ``_closed``) and silently
        drop the frame the frontend relies on to end the turn."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                f"[Broadcaster] Subscriber {self.session_id} queue full "
                f"(bound={_PER_SUBSCRIBER_QUEUE_BOUND}); dropping event "
                f"kind={event.get('kind') or event.get('type')!r}. Other "
                f"subscribers unaffected."
            )

    def close(self) -> None:
        """Mark closed — any further pushes are no-ops; events already
        queued (incl. the terminal `complete` frame published right
        before close) are still drained by the iterator, which
        terminates on the None sentinel."""
        if not self._closed:
            self._closed = True
            # Sentinel to wake any awaiting consumer. If the queue is
            # saturated, drop the oldest queued event to guarantee the
            # sentinel lands — a consumer blocked on get() with no
            # sentinel would hang forever.
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                with suppress(asyncio.QueueEmpty):
                    self._queue.get_nowait()
                with suppress(asyncio.QueueFull):
                    self._queue.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[dict]:
        """Yield queued events until the close sentinel (None) is
        reached. Termination is sentinel-only by design: checking
        ``self._closed`` here would discard events that were queued
        just before close() — exactly the terminal `complete` frame."""
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event


class Broadcaster:
    """Per-run fan-out hub. One instance per BackgroundRun.

    Public surface:
      * ``publish(event_dict)`` — push to every live subscriber
      * ``subscribe(session_id) -> Subscriber`` — register a new
        consumer; new subscriber receives the current_thinking_buffer
        snapshot (if any) BEFORE its first live event
      * ``unsubscribe(session_id)`` — remove a consumer (called when
        the WS disconnects)
      * ``set_current_thinking_buffer(text)`` — BackgroundRun updates
        this whenever the in-flight thinking segment grows or resets
      * ``close()`` — terminal; closes all subscribers and refuses new
        ones. Idempotent.

    The Broadcaster is intentionally simple: no priorities, no message
    coalescing (the upstream _ThinkingBatcher handles WS-tier merging
    before events ever reach here), no rate limiting.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._subscribers: dict[str, Subscriber] = {}
        self._current_thinking_buffer: str = ""
        self._closed: bool = False

    # ----- producer API --------------------------------------------------

    def publish(self, event: dict) -> None:
        """Fan-out an event to every active subscriber. Non-blocking —
        each subscriber's queue is the buffer."""
        if self._closed:
            return
        # Iterate over a snapshot so subscribers that disconnect mid-fanout
        # don't break the loop. push is synchronous put_nowait, so the
        # event is in every queue before publish() returns — a close()
        # right after publish() can never drop it.
        for sub in list(self._subscribers.values()):
            sub.push(event)

    def set_current_thinking_buffer(self, text: str) -> None:
        """Update the in-flight thinking segment snapshot. Called by
        BackgroundRun whenever the _ThinkingBatcher's segment buffer
        changes (every accumulation step + reset on segment end).

        Empty string means "no segment currently in flight". New
        subscribers will receive this string verbatim before joining
        the live stream, so they don't miss the part of the segment
        that hasn't been flushed to event_stream yet.
        """
        self._current_thinking_buffer = text

    # ----- consumer API --------------------------------------------------

    def subscribe(self, session_id: str) -> Subscriber:
        """Register a new subscriber. If the broadcaster is closed,
        return a subscriber whose iterator immediately terminates —
        callers don't need a separate branch."""
        if self._closed:
            sub = Subscriber(session_id)
            sub.close()
            return sub

        # Defensive: if the same session_id reconnects, replace the old
        # subscriber. The caller pattern is one-WS-one-Subscriber so
        # collisions should not happen, but handle gracefully.
        if session_id in self._subscribers:
            self._subscribers[session_id].close()

        sub = Subscriber(session_id)
        self._subscribers[session_id] = sub
        logger.info(
            f"[Broadcaster] run={self.run_id} subscriber={session_id} joined "
            f"(total subs={len(self._subscribers)}, "
            f"current_thinking_chars={len(self._current_thinking_buffer)})"
        )

        # Hand the new subscriber the current thinking segment snapshot
        # BEFORE any live event arrives. Without this, a reconnect
        # mid-segment loses the part of thinking that has been
        # accumulated but not yet flushed to event_stream.
        if self._current_thinking_buffer:
            sub.push({
                "type": "thinking_partial_replay",
                "content": self._current_thinking_buffer,
            })
        return sub

    def unsubscribe(self, session_id: str) -> None:
        """Remove a subscriber. Called when the WS disconnects.

        BackgroundRun lifecycle is unaffected — agent continues to run
        even with zero subscribers. That's the whole point of
        "用户关 tab 不死 agent"."""
        sub = self._subscribers.pop(session_id, None)
        if sub is None:
            return
        sub.close()
        logger.info(
            f"[Broadcaster] run={self.run_id} subscriber={session_id} left "
            f"(remaining subs={len(self._subscribers)})"
        )

    # ----- lifecycle API -------------------------------------------------

    def close(self) -> None:
        """Terminal — close every subscriber and refuse new ones.

        Called by BackgroundRun when the run reaches a terminal state
        (completed / cancelled / failed). Idempotent.
        """
        if self._closed:
            return
        self._closed = True
        logger.info(
            f"[Broadcaster] run={self.run_id} closing "
            f"({len(self._subscribers)} subs)"
        )
        for sub in list(self._subscribers.values()):
            sub.close()
        self._subscribers.clear()

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


__all__ = ["Broadcaster", "Subscriber"]
