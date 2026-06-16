"""
@file_name: test_funnel_success_event.py
@date: 2026-06-08
@description: message_round_trip_succeeded fires only on COMPLETED.
"""
import pytest

from xyz_agent_context.analytics import _hash_distinct_id
from xyz_agent_context.analytics._impl.fake_sink import FakeSink


async def _async_return(v):
    return v


@pytest.fixture
def fake_sink(monkeypatch):
    sink = FakeSink()
    import xyz_agent_context.analytics as analytics
    monkeypatch.setattr(analytics, "_get_sink_cached", lambda: sink)
    monkeypatch.setattr(analytics, "_opted_out", lambda user_id: _async_return(False))
    return sink


@pytest.mark.asyncio
async def test_success_helper_fires(fake_sink):
    from xyz_agent_context.agent_runtime.background_run import (
        _fire_message_success,
    )
    await _fire_message_success(user_id="u1", agent_id="a1", run_id="r1")
    evt = next(e for e in fake_sink.events
               if e[1] == "message_round_trip_succeeded")
    assert evt[0] == _hash_distinct_id("u1")
    assert evt[2].get("agent_id") == "a1"
    assert evt[2].get("run_id") == "r1"


@pytest.mark.asyncio
async def test_success_helper_ignores_empty_user(fake_sink):
    from xyz_agent_context.agent_runtime.background_run import (
        _fire_message_success,
    )
    await _fire_message_success(user_id="", agent_id="a1", run_id="r1")
    assert [e for e in fake_sink.events
            if e[1] == "message_round_trip_succeeded"] == []


# --- _had_fatal_error gate: a fatal error (e.g. no provider configured) ends
# the run naturally (STATE_COMPLETED) but produced no genuine reply, so it must
# NOT count as a successful round-trip. recovered/recoverable still delivered or
# survived, so they remain successful. ---

async def _seed_events_row(db, event_id):
    await db.insert("events", {
        "event_id": event_id,
        "trigger": "chat",
        "trigger_source": "test",
        "agent_id": "a_funnel",
        "user_id": "u_funnel",
        "state": "completed",
        "created_at": "2026-06-09T00:00:00",
        "updated_at": "2026-06-09T00:00:00",
    })


async def _make_bg(db, event_id):
    from xyz_agent_context.agent_runtime.background_run import BackgroundRun
    await _seed_events_row(db, event_id)
    bg = BackgroundRun(
        agent_id="a_funnel", user_id="u_funnel", input_preview="",
        db=db, active_runs={},
    )
    await bg._on_run_id_assigned(event_id)
    return bg


async def _cleanup(bg):
    import asyncio
    if bg._heartbeat_task:
        bg._heartbeat_task.cancel()
        try:
            await bg._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_fatal_error_event_marks_run_not_successful(db_client):
    bg = await _make_bg(db_client, "evt_fatal")
    assert bg._had_fatal_error is False
    await bg.emit({
        "type": "error", "severity": "fatal",
        "error_message": "No provider configured",
        "error_type": "NoProviderConfiguredError",
    })
    assert bg._had_fatal_error is True
    await _cleanup(bg)


@pytest.mark.asyncio
async def test_recovered_error_event_keeps_run_successful(db_client):
    bg = await _make_bg(db_client, "evt_recovered")
    await bg.emit({
        "type": "error", "severity": "recovered",
        "error_message": "fatal-class error but fallback produced a reply",
        "error_type": "api_error",
    })
    assert bg._had_fatal_error is False
    await _cleanup(bg)
