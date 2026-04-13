"""
@file_name: active_sessions.py
@author: NarraNexus
@date: 2026-04-13
@description: Process-local WebSocket session registry for dashboard concurrency view.

Protocol + in-memory implementation. NOT a xyz_agent_context.services-layer
service — lives only inside the FastAPI process memory and is populated and
cleaned by `backend/routes/websocket.py` lifecycle hooks.

Multi-worker deployments (WEB_CONCURRENCY > 1) undercount: each worker only
sees its own connections. Upgrade to a Redis-backed SessionRegistry impl in
that scenario (see design doc TDR-1).

Logging discipline: do NOT print SessionInfo fields `user_id / user_display /
channel` — they are PII. Only `session_id` and `agent_id` are log-safe.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SessionInfo:
    """In-memory record of one live WS connection."""

    session_id: str
    user_id: str
    user_display: str
    channel: str
    started_at: str  # ISO8601 UTC


class SessionRegistry(Protocol):
    """Interface so that future Redis-backed impl can drop in without changing callers."""

    async def add(self, agent_id: str, info: SessionInfo) -> None: ...
    async def remove(self, agent_id: str, session_id: str) -> None: ...
    async def snapshot(
        self, agent_ids: list[str]
    ) -> dict[str, list[SessionInfo]]: ...


class InProcessSessionRegistry:
    """In-memory registry; safe for single-process FastAPI deployments."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, SessionInfo]] = {}
        self._lock = asyncio.Lock()

    async def add(self, agent_id: str, info: SessionInfo) -> None:
        async with self._lock:
            self._data.setdefault(agent_id, {})[info.session_id] = info

    async def remove(self, agent_id: str, session_id: str) -> None:
        async with self._lock:
            if agent_id in self._data:
                self._data[agent_id].pop(session_id, None)
                if not self._data[agent_id]:
                    del self._data[agent_id]

    async def snapshot(
        self, agent_ids: list[str]
    ) -> dict[str, list[SessionInfo]]:
        async with self._lock:
            # Return a shallow copy so the caller can iterate without holding the lock.
            return {aid: list(self._data.get(aid, {}).values()) for aid in agent_ids}


_registry: SessionRegistry = InProcessSessionRegistry()


def get_session_registry() -> SessionRegistry:
    """Access the process-wide singleton. Callers MUST use this, never the `_registry` global directly."""
    return _registry
