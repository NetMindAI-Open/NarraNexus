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

import backend.routes.office_watch.proxy as owp


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


def test_evicts_stalest_not_oldest_by_age(monkeypatch):
    """Eviction must drop the LEAST-recently-active stream, not the oldest by
    age — a healthy long-open preview (fullscreen keeps the column stream live)
    must not lose its slot to a newer but idle one."""
    monkeypatch.setattr(owp, "MAX_SSE_STREAMS_PER_USER", 2)
    owp._active_streams.clear()

    async def run():
        s_old_but_active = _FakeSession()  # registered first (oldest by age)
        s_new_but_idle = _FakeSession()
        sid_old = await owp._register_sse_stream("A", s_old_but_active)
        sid_new = await owp._register_sse_stream("A", s_new_but_idle)
        # The old stream keeps receiving frames; the new one goes idle.
        owp._active_streams[sid_old]["last_active"] = 1000.0
        owp._active_streams[sid_new]["last_active"] = 1.0
        # A third stream arrives → the STALEST (sid_new) is evicted.
        s_third = _FakeSession()
        await owp._register_sse_stream("A", s_third)
        assert s_old_but_active.closed is False  # healthy, kept
        assert s_new_but_idle.closed is True  # stalest, evicted

    asyncio.run(run())
