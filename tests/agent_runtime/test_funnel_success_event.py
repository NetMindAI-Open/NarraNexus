"""
@file_name: test_funnel_success_event.py
@date: 2026-06-08
@description: message_round_trip_succeeded fires only on COMPLETED.
"""
import pytest


async def _async_return(v):
    return v


@pytest.fixture
def captured_events(monkeypatch):
    events = []
    import narranexus.platform.analytics as analytics

    async def _capture(**event):
        events.append(event)

    monkeypatch.setattr(analytics, "_persist_product_event", _capture)
    monkeypatch.setattr(analytics, "_opted_out", lambda user_id: _async_return(False))
    return events


@pytest.mark.asyncio
async def test_success_helper_fires(captured_events):
    from narranexus.platform.agent_runtime.background_run import (
        _fire_message_success,
    )
    await _fire_message_success(user_id="u1", agent_id="a1", run_id="r1")
    evt = next(e for e in captured_events
               if e["event"] == "message_round_trip_succeeded")
    assert evt["user_id"] == "u1"
    assert evt["properties"].get("agent_id") == "a1"
    assert evt["properties"].get("run_id") == "r1"


@pytest.mark.asyncio
async def test_success_helper_ignores_empty_user(captured_events):
    from narranexus.platform.agent_runtime.background_run import (
        _fire_message_success,
    )
    await _fire_message_success(user_id="", agent_id="a1", run_id="r1")
    assert [e for e in captured_events
            if e["event"] == "message_round_trip_succeeded"] == []


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
    from narranexus.platform.agent_runtime.background_run import BackgroundRun
    await _seed_events_row(db, event_id)
    bg = BackgroundRun(
        agent_id="a_funnel", user_id="u_funnel", input_preview="",
        db=db, active_runs={},
    )
    # Bind run_id the production way: the recorder spots Step 0.
    await bg.emit({
        "type": "progress", "step": "0", "status": "completed",
        "details": {"event_id": event_id},
    })
    return bg


async def _cleanup(bg):
    import asyncio
    hb = bg.recorder._heartbeat_task
    if hb:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_fatal_error_event_marks_run_not_successful(db_client):
    bg = await _make_bg(db_client, "evt_fatal")
    assert bg.recorder.had_fatal_error is False
    await bg.emit({
        "type": "error", "severity": "fatal",
        "error_message": "No provider configured",
        "error_type": "NoProviderConfiguredError",
    })
    assert bg.recorder.had_fatal_error is True
    await _cleanup(bg)


@pytest.mark.asyncio
async def test_recovered_error_event_keeps_run_successful(db_client):
    bg = await _make_bg(db_client, "evt_recovered")
    await bg.emit({
        "type": "error", "severity": "recovered",
        "error_message": "fatal-class error but fallback produced a reply",
        "error_type": "api_error",
    })
    assert bg.recorder.had_fatal_error is False
    await _cleanup(bg)
