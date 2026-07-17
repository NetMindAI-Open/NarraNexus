"""
@file_name: test_office_watch_sse_cap.py
@author: NetMind.AI
@date: 2026-07-14
@description: Per-user cap on concurrent office-watch SSE streams.

Each open live-preview tab holds one long-lived `/events` stream through the
shared backend proxy. Without a cap a user (or a leaked token) could pile up
streams and exhaust backend connections/fds. `_register_sse_stream` caps them
per user and, on the (N+1)th, evicts the user's OLDEST by closing its aiohttp
session — per-user so one user can never evict another's stream.
"""

from __future__ import annotations

import asyncio

import backend.routes.office_watch_proxy as owp


class _FakeSession:
    """Stand-in for aiohttp.ClientSession — records that eviction closed it."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _active_for(user_id: str) -> list[int]:
    return sorted(sid for sid, e in owp._active_streams.items() if e["user_id"] == user_id)


def test_evicts_oldest_over_cap(monkeypatch):
    monkeypatch.setattr(owp, "MAX_SSE_STREAMS_PER_USER", 3)
    owp._active_streams.clear()

    async def run():
        sessions = [_FakeSession() for _ in range(5)]
        for s in sessions:
            await owp._register_sse_stream("A", s)
        # Only the newest 3 survive; the oldest 2 were evicted (closed).
        assert len(_active_for("A")) == 3
        assert [s.closed for s in sessions] == [True, True, False, False, False]

    asyncio.run(run())


def test_cap_is_per_user(monkeypatch):
    monkeypatch.setattr(owp, "MAX_SSE_STREAMS_PER_USER", 3)
    owp._active_streams.clear()

    async def run():
        a_sessions = [_FakeSession() for _ in range(3)]
        for s in a_sessions:
            await owp._register_sse_stream("A", s)
        # A different user opening a stream must NOT evict A's streams.
        b = _FakeSession()
        await owp._register_sse_stream("B", b)
        assert len(_active_for("A")) == 3
        assert all(not s.closed for s in a_sessions)
        assert not b.closed

    asyncio.run(run())


def test_unregister_frees_a_slot(monkeypatch):
    monkeypatch.setattr(owp, "MAX_SSE_STREAMS_PER_USER", 3)
    owp._active_streams.clear()

    async def run():
        sids = [await owp._register_sse_stream("A", _FakeSession()) for _ in range(3)]
        owp._unregister_sse_stream(sids[0])
        assert len(_active_for("A")) == 2
        # A fresh stream now fits without eviction.
        fresh = _FakeSession()
        await owp._register_sse_stream("A", fresh)
        assert len(_active_for("A")) == 3
        assert not fresh.closed

    asyncio.run(run())
