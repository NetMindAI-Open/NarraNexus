"""
@file_name: test_background_run_persistence.py
@author: Bin Liang
@date: 2026-05-13
@description: Integration tests for BackgroundRun's persistence layer.

We bypass the full AgentRuntime stack (way too expensive for unit-level
tests) and exercise the persistence pathway by feeding events directly
into ``bg.emit(...)`` after manually triggering ``_on_run_id_assigned``.

Coverage targets:
  * events row state transitions (running → completed/cancelled/failed)
  * event_stream rows for tool_call / tool_output / thinking_segment
  * thinking segments only flush at type switches (組合 B)
  * Broadcaster integration: subscribers see live events and the
    current_thinking_buffer snapshot is exposed
  * Cleanup removes the run from the active_runs registry
"""
from __future__ import annotations

import asyncio
import json

import pytest

from xyz_agent_context.agent_runtime.background_run import (
    BackgroundRun,
    STATE_COMPLETED,
    STATE_RUNNING,
)


async def _seed_events_row(db, event_id: str, agent_id: str = "agent_test", user_id: str = "u_test"):
    """The real Step 0 inserts the events row before BackgroundRun
    learns the event_id. For test purposes we pre-seed it ourselves."""
    await db.insert(
        "events",
        {
            "event_id": event_id,
            "trigger": "chat",
            "trigger_source": "test",
            "agent_id": agent_id,
            "user_id": user_id,
            "state": "completed",  # default — _on_run_id_assigned flips to running
            "created_at": "2026-05-13T00:00:00",
            "updated_at": "2026-05-13T00:00:00",
        },
    )


@pytest.mark.asyncio
async def test_on_run_id_assigned_flips_state_to_running(db_client):
    await _seed_events_row(db_client, "evt_run1")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="hello",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run1")

    assert bg.run_id == "evt_run1"
    assert "evt_run1" in active_runs
    row = await db_client.get_one("events", {"event_id": "evt_run1"})
    assert row["state"] == STATE_RUNNING
    assert bg.ready_event.is_set()
    # Cleanup
    if bg._heartbeat_task:
        bg._heartbeat_task.cancel()
        try:
            await bg._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_tool_call_event_writes_event_stream_row(db_client):
    await _seed_events_row(db_client, "evt_run2")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run2")

    await bg.emit({
        "type": "progress",
        "step": "3.4.1",
        "title": "🔧 Bash",
        "description": "Running shell command",
        "details": {"tool_name": "Bash", "arguments": {"command": "ls"}},
    })

    rows = await db_client.get("event_stream", {"event_id": "evt_run2"})
    assert len(rows) == 1
    assert rows[0]["kind"] == "tool_call"
    payload = json.loads(rows[0]["payload"])
    assert payload["tool_name"] == "Bash"
    assert payload["arguments"]["command"] == "ls"

    # tool_call_count incremented
    events_row = await db_client.get_one("events", {"event_id": "evt_run2"})
    assert events_row["tool_call_count"] == 1

    if bg._heartbeat_task:
        bg._heartbeat_task.cancel()
        try:
            await bg._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_thinking_segment_only_flushes_on_type_switch(db_client):
    """Critical 組合 B invariant: many thinking events buffer into a
    single segment; only when a non-thinking event arrives does the
    segment get persisted as one row."""
    await _seed_events_row(db_client, "evt_run3")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run3")

    # 5 thinking events — should accumulate, NOT persist
    for chunk in ["hello ", "world ", "this ", "is ", "thinking"]:
        await bg.emit({"type": "agent_thinking", "thinking_content": chunk})

    rows = await db_client.get("event_stream", {"event_id": "evt_run3"})
    assert len(rows) == 0, "thinking should still be buffered, no stream rows yet"
    assert bg._current_thinking_segment, "buffer should hold the chunks"

    # Now a tool_call arrives — segment flushes, then tool_call row
    await bg.emit({
        "type": "progress",
        "step": "3.4.1",
        "title": "🔧 Read",
        "description": "Reading file",
        "details": {"tool_name": "Read", "arguments": {}},
    })

    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_run3"}),
        key=lambda r: r["seq"],
    )
    assert len(rows) == 2
    assert rows[0]["kind"] == "thinking_segment"
    assert rows[0]["payload"] == "hello world this is thinking"
    assert rows[1]["kind"] == "tool_call"

    # Segment buffer should be cleared after flush
    assert not bg._current_thinking_segment

    if bg._heartbeat_task:
        bg._heartbeat_task.cancel()
        try:
            await bg._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_finalize_writes_terminal_state_and_removes_from_registry(db_client):
    await _seed_events_row(db_client, "evt_run4")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run4")
    assert "evt_run4" in active_runs

    bg.state = STATE_COMPLETED
    await bg._finalize()

    row = await db_client.get_one("events", {"event_id": "evt_run4"})
    assert row["state"] == STATE_COMPLETED
    assert row["finished_at"] is not None
    # Removed from registry
    assert "evt_run4" not in active_runs
    # Broadcaster closed
    assert bg.broadcaster.is_closed


@pytest.mark.asyncio
async def test_finalize_broadcasts_terminal_complete_frame(db_client):
    """The live WS path has no other end-of-run signal: subscribers must
    receive a `complete` frame (with the terminal state) before the
    broadcaster closes. Without it the frontend treats the server-side
    close as a passive disconnect and spins up the reconnect machinery
    on every normal turn end (duplicate user bubble + stuck spinner)."""
    await _seed_events_row(db_client, "evt_run6")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run6")

    sub = bg.broadcaster.subscribe("ws-live")

    bg.state = STATE_COMPLETED
    await bg._finalize()

    received = []
    async for e in sub:
        received.append(e)

    completes = [e for e in received if e.get("type") == "complete"]
    assert len(completes) == 1
    assert completes[0]["state"] == STATE_COMPLETED


@pytest.mark.asyncio
async def test_broadcaster_current_thinking_buffer_reflects_segment(db_client):
    """While a thinking segment is being accumulated, the broadcaster's
    current_thinking_buffer must mirror it so a mid-segment subscriber
    gets the full partial."""
    await _seed_events_row(db_client, "evt_run5")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="",
        db=db_client,
        active_runs=active_runs,
    )
    await bg._on_run_id_assigned("evt_run5")

    await bg.emit({"type": "agent_thinking", "thinking_content": "part1 "})
    await bg.emit({"type": "agent_thinking", "thinking_content": "part2"})
    assert bg.broadcaster._current_thinking_buffer == "part1 part2"

    # A non-thinking event clears the buffer (after persisting the segment)
    await bg.emit({
        "type": "progress",
        "step": "3.4.1",
        "title": "🔧 Read",
        "description": "",
        "details": {"tool_name": "Read", "arguments": {}},
    })
    assert bg.broadcaster._current_thinking_buffer == ""

    if bg._heartbeat_task:
        bg._heartbeat_task.cancel()
        try:
            await bg._heartbeat_task
        except asyncio.CancelledError:
            pass
