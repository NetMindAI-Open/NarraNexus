"""
@file_name: test_artifact_event_drain.py
@date: 2026-08-18
@description: TDD for BackgroundRun's artifact event outbox drain.

Contract (spec 2026-08-18-artifact-events-inventory-pointer §3): rows staged
in instance_artifact_events by any process are re-emitted through
BackgroundRun.emit right after a tool-output event, riding the normal
recorder+broadcaster pipeline; delivered rows are marked consumed. The drain
is gated to tool-output events only (text deltas stream at high frequency —
a DB query per delta is forbidden) and is best-effort: a drain failure never
breaks the run loop.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.agent_runtime.background_run import BackgroundRun


def _tool_output_event() -> dict:
    return {
        "type": "agent_tool_output",
        "tool_name": "some_tool",
        "output": "ok",
    }


def _text_delta_event() -> dict:
    return {"type": "agent_response", "delta": "hel"}


async def _stage(db, agent_id: str, action: str = "registered") -> None:
    await db.insert(
        "instance_artifact_events",
        {
            "agent_id": agent_id,
            "payload_json": json.dumps(
                {
                    "type": "artifact_changed",
                    "action": action,
                    "external": False,
                    "artifact": {"artifact_id": "art_wired", "agent_id": agent_id},
                }
            ),
        },
    )


def _make_run(db_client, agent_id: str = "agent_test") -> BackgroundRun:
    bg = BackgroundRun(
        agent_id=agent_id,
        user_id="u_test",
        input_preview="hello",
        db=db_client,
        active_runs={},
    )
    return bg


def _capture_publishes(bg: BackgroundRun) -> list:
    captured: list = []
    original = bg.broadcaster.publish

    def _spy(event):
        captured.append(event)
        return original(event)

    bg.broadcaster.publish = _spy  # type: ignore[method-assign]
    return captured


@pytest.mark.asyncio
async def test_tool_output_drains_staged_events(db_client):
    bg = _make_run(db_client)
    captured = _capture_publishes(bg)
    await _stage(db_client, "agent_test")

    await bg.on_event(_tool_output_event())

    assert any(e.get("type") == "artifact_changed" for e in captured)
    rows = await db_client.execute(
        "SELECT consumed_at FROM instance_artifact_events WHERE agent_id = %s",
        params=("agent_test",),
        fetch=True,
    )
    assert rows and all(r["consumed_at"] is not None for r in rows)


@pytest.mark.asyncio
async def test_text_delta_does_not_touch_the_outbox(db_client):
    bg = _make_run(db_client)
    captured = _capture_publishes(bg)
    await _stage(db_client, "agent_test")

    await bg.on_event(_text_delta_event())

    assert not any(e.get("type") == "artifact_changed" for e in captured)
    rows = await db_client.execute(
        "SELECT consumed_at FROM instance_artifact_events WHERE agent_id = %s",
        params=("agent_test",),
        fetch=True,
    )
    assert rows and all(r["consumed_at"] is None for r in rows)


@pytest.mark.asyncio
async def test_drain_only_takes_own_agents_rows(db_client):
    bg = _make_run(db_client, agent_id="agent_test")
    captured = _capture_publishes(bg)
    await _stage(db_client, "agent_other")

    await bg.on_event(_tool_output_event())

    assert not any(e.get("type") == "artifact_changed" for e in captured)
    rows = await db_client.execute(
        "SELECT consumed_at FROM instance_artifact_events WHERE agent_id = %s",
        params=("agent_other",),
        fetch=True,
    )
    assert rows and rows[0]["consumed_at"] is None


@pytest.mark.asyncio
async def test_drain_failure_never_breaks_the_run_loop(db_client, monkeypatch):
    bg = _make_run(db_client)
    await _stage(db_client, "agent_test")

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(bg.db, "execute", _boom)
    # Must not raise — the tool-output event itself still goes through.
    await bg.on_event(_tool_output_event())


@pytest.mark.asyncio
async def test_legacy_progress_tool_output_also_drains(db_client):
    """The legacy protocol spells tool output as progress+details.output —
    the gate must recognise both spellings (run_recorder._classify_event is
    the single source of truth)."""
    bg = _make_run(db_client)
    captured = _capture_publishes(bg)
    await _stage(db_client, "agent_test")

    await bg.on_event(
        {"type": "progress", "details": {"tool_name": "t", "output": "done"}}
    )

    assert any(e.get("type") == "artifact_changed" for e in captured)
