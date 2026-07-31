"""
@file_name: test_run_recorder.py
@author:
@date: 2026-07-31
@description: RunRecorder — the transport-agnostic persistence half of
run observability.

Coverage targets:
  * run_id late-binding from the Step-0 progress message (events row
    flips to running, heartbeat starts, on_run_id fires)
  * event_stream rows for tool_call / thinking_segment (組合 B: whole
    segments only, flushed on type switch)
  * finalize semantics: completed fills final_output only when empty;
    cancelled records the reason; failed records the (redacted) error;
    idempotent; record() becomes a no-op afterwards
  * sweep_stale_runs flips only heartbeat-dead running rows
  * the recording kill switch env
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from xyz_agent_context.agent_runtime.run_recorder import (
    RECORDING_DISABLED_ENV,
    RunRecorder,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_RUNNING,
    recording_enabled,
    sweep_stale_runs,
)
from xyz_agent_context.utils.timezone import utc_now


def _step0_progress(event_id: str) -> dict:
    return {
        "type": "progress",
        "step": "0",
        "status": "completed",
        "title": "Initialized",
        "details": {"event_id": event_id},
    }


async def _seed_events_row(db, event_id: str, **overrides):
    row = {
        "event_id": event_id,
        "trigger": "lark",
        "trigger_source": "test",
        "agent_id": "agent_test",
        "user_id": "u_test",
        "state": "completed",  # Step-0 default — bind flips to running
        "created_at": "2026-07-31T00:00:00",
        "updated_at": "2026-07-31T00:00:00",
    }
    row.update(overrides)
    await db.insert("events", row)


async def _stop(recorder: RunRecorder):
    """Cancel the heartbeat without going through finalize."""
    if recorder._heartbeat_task and not recorder._heartbeat_task.done():
        recorder._heartbeat_task.cancel()
        try:
            await recorder._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_step0_progress_binds_run_id_and_flips_running(db_client):
    await _seed_events_row(db_client, "evt_rr1")
    seen: list[str] = []

    async def on_run_id(run_id: str) -> None:
        seen.append(run_id)

    rec = RunRecorder(db=db_client, on_run_id=on_run_id)
    await rec.record(_step0_progress("evt_rr1"))

    assert rec.run_id == "evt_rr1"
    assert seen == ["evt_rr1"]
    row = await db_client.get_one("events", {"event_id": "evt_rr1"})
    assert row["state"] == STATE_RUNNING
    assert row["started_at"] is not None
    assert rec._heartbeat_task is not None
    await _stop(rec)


@pytest.mark.asyncio
async def test_thinking_segment_flushes_only_on_type_switch(db_client):
    """組合 B invariant: many thinking events buffer into ONE segment row,
    persisted only when a non-thinking event arrives."""
    await _seed_events_row(db_client, "evt_rr2")
    buffers: list[str] = []
    rec = RunRecorder(db=db_client, on_thinking_buffer=buffers.append)
    await rec.record(_step0_progress("evt_rr2"))

    for chunk in ["hello ", "world"]:
        await rec.record({"type": "agent_thinking", "thinking_content": chunk})
    # Mid-segment: transport mirror sees the growing partial, no rows yet.
    assert buffers[-1] == "hello world"
    rows = await db_client.get("event_stream", {"event_id": "evt_rr2"})
    assert [r for r in rows if r["kind"] == "thinking_segment"] == []

    await rec.record({
        "type": "progress", "step": "3.4.1", "title": "🔧 Read",
        "details": {"tool_name": "Read", "arguments": {"file": "x"}},
    })
    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_rr2"}),
        key=lambda r: r["seq"],
    )
    # progress(step0) row + thinking_segment + tool_call, in order.
    kinds = [r["kind"] for r in rows]
    assert kinds[-2:] == ["thinking_segment", "tool_call"]
    seg = next(r for r in rows if r["kind"] == "thinking_segment")
    assert seg["payload"] == "hello world"
    assert buffers[-1] == ""  # mirror reset on flush

    events_row = await db_client.get_one("events", {"event_id": "evt_rr2"})
    assert events_row["tool_call_count"] == 1
    await _stop(rec)


@pytest.mark.asyncio
async def test_finalize_completed_fills_final_output_only_if_empty(db_client):
    await _seed_events_row(db_client, "evt_rr3")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr3"))
    await rec.record({"type": "agent_response", "delta": "hi "})
    await rec.record({"type": "agent_response", "delta": "there"})
    await rec.finalize(STATE_COMPLETED)

    row = await db_client.get_one("events", {"event_id": "evt_rr3"})
    assert row["state"] == STATE_COMPLETED
    assert row["final_output"] == "hi there"
    assert row["finished_at"] is not None

    # Idempotent + record() is a no-op afterwards.
    await rec.finalize(STATE_FAILED)
    await rec.record({"type": "agent_response", "delta": "late"})
    row = await db_client.get_one("events", {"event_id": "evt_rr3"})
    assert row["state"] == STATE_COMPLETED
    assert row["final_output"] == "hi there"


@pytest.mark.asyncio
async def test_finalize_never_overwrites_existing_final_output(db_client):
    # step_4 persists its own final_output; the recorder must not clobber it.
    await _seed_events_row(db_client, "evt_rr4", final_output="canonical")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr4"))
    await rec.record({"type": "agent_response", "delta": "recorder view"})
    await rec.finalize(STATE_COMPLETED)
    row = await db_client.get_one("events", {"event_id": "evt_rr4"})
    assert row["final_output"] == "canonical"


@pytest.mark.asyncio
async def test_finalize_cancelled_and_failed_record_causes(db_client):
    await _seed_events_row(db_client, "evt_rr5")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr5"))
    await rec.finalize(STATE_CANCELLED, cancel_reason="user pressed stop")
    row = await db_client.get_one("events", {"event_id": "evt_rr5"})
    assert row["state"] == STATE_CANCELLED
    assert row["error_message"] == "user pressed stop"

    await _seed_events_row(db_client, "evt_rr6")
    rec2 = RunRecorder(db=db_client)
    await rec2.record(_step0_progress("evt_rr6"))
    await rec2.finalize(
        STATE_FAILED, error_type="TimeoutError", error_message="read timed out",
    )
    row2 = await db_client.get_one("events", {"event_id": "evt_rr6"})
    assert row2["state"] == STATE_FAILED
    assert "read timed out" in (row2["error_message"] or "")


@pytest.mark.asyncio
async def test_fatal_error_event_sets_flags(db_client):
    rec = RunRecorder(db=db_client)
    assert rec.had_fatal_error is False
    await rec.record({
        "type": "error", "severity": "fatal",
        "error_type": "NoProviderConfiguredError",
        "error_message": "No provider configured",
    })
    assert rec.had_fatal_error is True
    assert rec.last_error_type == "NoProviderConfiguredError"
    # recovered severities never void the turn
    rec2 = RunRecorder(db=db_client)
    await rec2.record({
        "type": "error", "severity": "recovered",
        "error_type": "api_error", "error_message": "fallback replied",
    })
    assert rec2.had_fatal_error is False


@pytest.mark.asyncio
async def test_progress_rows_replayable(db_client):
    """Progress frames persist as stream rows so observers can replay
    the pre-loop pipeline phases."""
    await _seed_events_row(db_client, "evt_rr7")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr7"))
    await rec.record({
        "type": "progress", "step": "1", "title": "Loading context",
        "details": {},
    })
    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_rr7"}),
        key=lambda r: r["seq"],
    )
    assert [r["kind"] for r in rows] == ["progress", "progress"]
    payload = json.loads(rows[1]["payload"])
    assert payload["step"] == "1"
    await _stop(rec)


@pytest.mark.asyncio
async def test_sweep_flips_only_heartbeat_dead_rows(db_client):
    fresh = utc_now()
    stale = utc_now() - timedelta(seconds=600)
    await _seed_events_row(
        db_client, "evt_alive", state="running",
        started_at=fresh, last_event_at=fresh,
    )
    await _seed_events_row(
        db_client, "evt_dead", state="running",
        started_at=stale, last_event_at=stale,
    )
    flipped = await sweep_stale_runs(db_client)
    assert flipped == 1
    alive = await db_client.get_one("events", {"event_id": "evt_alive"})
    dead = await db_client.get_one("events", {"event_id": "evt_dead"})
    assert alive["state"] == "running"
    assert dead["state"] == STATE_FAILED
    assert "run lost" in (dead["error_message"] or "")


def test_recording_kill_switch(monkeypatch):
    monkeypatch.delenv(RECORDING_DISABLED_ENV, raising=False)
    assert recording_enabled() is True
    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    assert recording_enabled() is False
    monkeypatch.setenv(RECORDING_DISABLED_ENV, "false")
    assert recording_enabled() is True
