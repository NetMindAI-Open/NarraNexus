"""
@file_name: test_background_run_persistence.py
@author: Bin Liang
@date: 2026-05-13
@description: Integration tests for BackgroundRun's persistence layer.

We bypass the full AgentRuntime stack (way too expensive for unit-level
tests) and exercise the persistence pathway by feeding events directly
into ``bg.emit(...)``. run_id binding happens the same way it does in
production: the recorder spots the Step-0 progress message.

Coverage targets:
  * events row state transitions (running → completed/cancelled/failed)
  * event_stream rows for tool_call / thinking_segment (persistence is
    delegated to RunRecorder — its own edge cases live in
    test_run_recorder.py; here we lock the COMPOSITION)
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


def _step0_progress(event_id: str) -> dict:
    return {
        "type": "progress",
        "step": "0",
        "status": "completed",
        "title": "Initialized",
        "details": {"event_id": event_id},
    }


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
            "state": "completed",  # default — run_id binding flips to running
            "created_at": "2026-05-13T00:00:00",
            "updated_at": "2026-05-13T00:00:00",
        },
    )


async def _cleanup(bg: BackgroundRun):
    hb = bg.recorder._heartbeat_task
    if hb:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_step0_emit_binds_run_id_and_flips_state_to_running(db_client):
    await _seed_events_row(db_client, "evt_run1")
    active_runs: dict = {}
    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="hello",
        db=db_client,
        active_runs=active_runs,
    )
    await bg.emit(_step0_progress("evt_run1"))

    assert bg.run_id == "evt_run1"
    assert "evt_run1" in active_runs
    assert bg.broadcaster.run_id == "evt_run1"
    row = await db_client.get_one("events", {"event_id": "evt_run1"})
    assert row["state"] == STATE_RUNNING
    assert bg.ready_event.is_set()
    await _cleanup(bg)


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
    await bg.emit(_step0_progress("evt_run2"))

    await bg.emit({
        "type": "progress",
        "step": "3.4.1",
        "title": "🔧 Bash",
        "description": "Running shell command",
        "details": {"tool_name": "Bash", "arguments": {"command": "ls"}},
    })

    rows = [
        r for r in await db_client.get("event_stream", {"event_id": "evt_run2"})
        if r["kind"] == "tool_call"
    ]
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload"])
    assert payload["tool_name"] == "Bash"
    assert payload["arguments"]["command"] == "ls"

    # tool_call_count incremented (delegate property)
    events_row = await db_client.get_one("events", {"event_id": "evt_run2"})
    assert events_row["tool_call_count"] == 1
    assert bg.tool_call_count == 1

    await _cleanup(bg)


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
    await bg.emit(_step0_progress("evt_run3"))

    # 5 thinking events — should accumulate, NOT persist
    for chunk in ["hello ", "world ", "this ", "is ", "thinking"]:
        await bg.emit({"type": "agent_thinking", "thinking_content": chunk})

    rows = [
        r for r in await db_client.get("event_stream", {"event_id": "evt_run3"})
        if r["kind"] == "thinking_segment"
    ]
    assert rows == [], "thinking should still be buffered, no segment rows yet"
    assert bg.recorder._segment, "buffer should hold the chunks"

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
    kinds = [r["kind"] for r in rows]
    assert kinds[-2:] == ["thinking_segment", "tool_call"]
    seg = next(r for r in rows if r["kind"] == "thinking_segment")
    assert seg["payload"] == "hello world this is thinking"

    # Segment buffer should be cleared after flush
    assert not bg.recorder._segment

    await _cleanup(bg)


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
    await bg.emit(_step0_progress("evt_run4"))
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
    await bg.emit(_step0_progress("evt_run6"))

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
    await bg.emit(_step0_progress("evt_run5"))

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

    await _cleanup(bg)
