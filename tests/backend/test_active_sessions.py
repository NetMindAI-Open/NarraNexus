"""
@file_name: test_active_sessions.py
@description: T02-T05 — SessionInfo dataclass + SessionRegistry Protocol + InProcessSessionRegistry + singleton.
"""
import asyncio
import pytest

from backend.state.active_sessions import (
    InProcessSessionRegistry,
    SessionInfo,
    SessionRegistry,
    get_session_registry,
)


def test_session_info_frozen():
    s = SessionInfo(
        session_id="s1",
        user_id="u1",
        user_display="Alice",
        channel="web",
        started_at="2026-04-13T00:00:00Z",
    )
    with pytest.raises(Exception):
        s.session_id = "other"  # frozen dataclass


def test_session_registry_is_protocol():
    # Protocol exposes three async methods
    assert hasattr(SessionRegistry, "add")
    assert hasattr(SessionRegistry, "remove")
    assert hasattr(SessionRegistry, "snapshot")


@pytest.mark.asyncio
async def test_add_remove_snapshot():
    reg = InProcessSessionRegistry()
    info = SessionInfo("s1", "u1", "Alice", "web", "2026-04-13T00:00:00Z")
    await reg.add("agent_a", info)
    snap = await reg.snapshot(["agent_a", "agent_b"])
    assert len(snap["agent_a"]) == 1 and snap["agent_a"][0].session_id == "s1"
    assert snap["agent_b"] == []
    await reg.remove("agent_a", "s1")
    snap = await reg.snapshot(["agent_a"])
    assert snap["agent_a"] == []


@pytest.mark.asyncio
async def test_snapshot_returns_copy_not_reference():
    reg = InProcessSessionRegistry()
    info = SessionInfo("s1", "u1", "A", "web", "t")
    await reg.add("agent_a", info)
    snap1 = await reg.snapshot(["agent_a"])
    snap1["agent_a"].clear()  # mutate the copy
    snap2 = await reg.snapshot(["agent_a"])
    assert len(snap2["agent_a"]) == 1


@pytest.mark.asyncio
async def test_concurrent_add_remove_no_race():
    reg = InProcessSessionRegistry()

    async def adder(i):
        await reg.add(
            "agent_a",
            SessionInfo(f"s{i}", f"u{i}", "X", "web", "t"),
        )

    async def remover(i):
        await reg.remove("agent_a", f"s{i}")

    await asyncio.gather(*[adder(i) for i in range(50)])
    await asyncio.gather(*[remover(i) for i in range(25)])
    snap = await reg.snapshot(["agent_a"])
    assert len(snap["agent_a"]) == 25


def test_get_session_registry_is_singleton():
    r1 = get_session_registry()
    r2 = get_session_registry()
    assert r1 is r2
