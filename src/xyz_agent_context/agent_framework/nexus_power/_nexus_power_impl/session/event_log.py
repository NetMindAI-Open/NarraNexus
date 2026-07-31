"""
@file_name: event_log.py
@author: Bin Liang
@date: 2026-07-29
@description: EventLogWriter implementations — the two-track log outlet
(constraint C1).

The executor stays a stateless worker (iron rule #20): v1 hands every
event to an injected async sink (the runner's NDJSON writer, or the
in-process driver's collector); the control plane persists the stream
into ``nexus_events`` (platform side). Appending must never block the
event flow noticeably — "logging is pass-through, not a fork".
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.events import LoopEvent

AsyncSink = Callable[[dict[str, Any]], Awaitable[None]]


def event_to_row(thread_id: str, event: LoopEvent) -> dict[str, Any]:
    """The wire/row form of one event — identical for NDJSON and the
    future ``nexus_events`` table ("entry is schema")."""
    return {
        "thread_id": thread_id,
        "seq": event.seq,
        "track": event.track,
        "type": event.type,
        "payload": event.payload,
        "usage": asdict(event.usage) if event.usage is not None else None,
    }


class StreamingEventLogWriter:
    """Streams rows to an async sink (append-only, seq-idempotent by
    construction upstream)."""

    def __init__(self, thread_id: str, sink: AsyncSink) -> None:
        self._thread_id = thread_id
        self._sink = sink

    async def append(self, event: LoopEvent) -> None:
        await self._sink(event_to_row(self._thread_id, event))

    async def flush(self) -> None:
        return None


class NullEventLogWriter:
    """Discards everything — an explicit choice for tests, never a default."""

    async def append(self, event: LoopEvent) -> None:
        return None

    async def flush(self) -> None:
        return None


def ndjson_line(row: dict[str, Any]) -> str:
    """One NDJSON line (no length assumptions — readers must buffer)."""
    return json.dumps(row, ensure_ascii=False, default=str)


#: How many turn logs a directory keeps. One file per turn accumulates in
#: the agent's WORKSPACE — which in cloud is a shared volume that nothing
#: else ever prunes — so the directory is bounded here. This caps disk, not
#: the agent: no turn is ever refused, the oldest EVIDENCE is simply aged
#: out once there is plenty of it.
LOG_RETENTION = 200


def prune_turn_logs(directory: str, keep: int = LOG_RETENTION) -> int:
    """Drop all but the ``keep`` newest ``*.ndjson`` files. Returns the
    number removed.

    Best-effort by construction: a turn log that vanishes under us (a
    concurrent turn pruning the same directory) is exactly the outcome we
    wanted, and a permission error on someone else's file must never take
    down the turn that is merely trying to tidy up.
    """
    import os

    try:
        entries = [e for e in os.scandir(directory) if e.name.endswith(".ndjson")]
        if len(entries) <= keep:
            return 0
        # stat() belongs INSIDE the guard: a concurrent turn pruning the
        # same directory can delete a file between scandir and stat, and
        # a FileNotFoundError escaping here would kill a turn before it
        # started — from tidying up (2026-07-29 review). Best-effort has
        # to mean it everywhere, not just where it was convenient.
        entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    removed = 0
    for entry in entries[keep:]:
        try:
            os.remove(entry.path)
            removed += 1
        except OSError:
            continue
    return removed


class FileEventLogWriter:
    """Appends NDJSON rows to a local file — the local-mode truth store
    (cloud lands rows in the control plane instead). Line-buffered; the
    turn-done flush is guaranteed by the assembly's ``finally``."""

    def __init__(self, thread_id: str, path: str, keep: int = LOG_RETENTION) -> None:
        import os

        self._thread_id = thread_id
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        prune_turn_logs(directory, keep)
        self._handle = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115

    async def append(self, event: LoopEvent) -> None:
        self._handle.write(ndjson_line(event_to_row(self._thread_id, event)) + "\n")

    async def flush(self) -> None:
        try:
            self._handle.flush()
            self._handle.close()
        except ValueError:  # already closed — flush is idempotent
            pass
