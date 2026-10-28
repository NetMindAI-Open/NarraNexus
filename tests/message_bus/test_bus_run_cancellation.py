"""
@file_name: test_bus_run_cancellation.py
@date: 2026-08-07
@description: A bus run can be stopped, and a stop is not a failure.

Pins the four facts the owner's stop depends on at this layer:

  * a live token reaches AgentRuntime (before this, the trigger passed none,
    so the runtime made its own no-op token and NOTHING could stop a
    bus-driven run — the 8-minute black box in the incident)
  * the token is registered with CancelWatcher under the run's id as soon as
    Step 0 mints it, and unregistered however the turn exits
  * CancelledByUser advances the processing cursor. Without this the stopped
    message stays pending and the next poll restarts the very run the owner
    just stopped — "stop" would read as "it restarted itself"
  * CancelledByUser is NOT recorded as a delivery failure: three stops would
    otherwise poison the message and `get_pending_messages` would filter it
    out forever, and the owner would get a "your agent broke" notice for
    pressing stop
"""

from __future__ import annotations

from types import SimpleNamespace

from narranexus.platform.agent_runtime.run_collector import RunCollection
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.agent_runtime.cancel_watcher import reset_cancel_watcher
from narranexus.platform.agent_runtime.cancellation import CancelledByUser
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.message_bus_trigger import (
    TEAM_ROOM_OWNER_PREFIX,
    MessageBusTrigger,
    TurnResult,
)
from narranexus.platform.message_bus.schemas import BusMessage


@pytest.fixture(autouse=True)
def _fresh_watcher():
    reset_cancel_watcher()
    yield
    reset_cancel_watcher()


def _patch_db_factory(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _async_db)


async def _seed_agent(db_client, agent_id="agent_a", owner="user_x"):
    await db_client.insert("agents", {"agent_id": agent_id, "agent_name": "A", "created_by": owner})


async def _handle(trigger, channel_owner=f"{TEAM_ROOM_OWNER_PREFIX}team_1"):
    msg = BusMessage(
        message_id="m1",
        channel_id="ch_1",
        from_agent="usr_user_x",
        content="@A do the long thing",
    )
    await trigger._handle_channel_batch(
        "agent_a",
        "ch_1",
        [msg],
        msg,
        channel_owner=channel_owner,
    )


@pytest.mark.asyncio
async def test_a_live_token_reaches_the_runtime(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    seen: dict = {}

    async def _record(*args, **kwargs):
        seen.update(kwargs)
        return TurnResult(text="", event_id=None, delivered=True)

    monkeypatch.setattr(trigger, "_invoke_runtime", _record)

    await _handle(trigger)

    token = seen.get("cancellation")
    assert token is not None
    # A real token, not a placeholder — it must be firable from outside.
    assert hasattr(token, "cancel") and not token.is_cancelled


@pytest.mark.asyncio
async def test_token_is_registered_under_the_run_id_then_released(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    trigger = MessageBusTrigger(bus=LocalMessageBus(backend=db_client._backend))
    watched_during_run: dict = {}

    async def _record(*args, **kwargs):
        # Step 0 mints the id and the trigger forwards it through on_event_id.
        await kwargs["on_event_id"]("evt_run_1")
        from narranexus.platform.agent_runtime.cancel_watcher import get_cancel_watcher

        watcher = get_cancel_watcher(db_client)
        watched_during_run["ids"] = list(watcher._tokens.keys())
        return TurnResult(text="", event_id="evt_run_1", delivered=True)

    monkeypatch.setattr(trigger, "_invoke_runtime", _record)

    await _handle(trigger)

    from narranexus.platform.agent_runtime.cancel_watcher import get_cancel_watcher

    assert watched_during_run["ids"] == ["evt_run_1"]
    # Released on exit — a stale entry would keep the poll loop alive forever.
    assert not get_cancel_watcher(db_client).watching


@pytest.mark.asyncio
async def test_stop_advances_the_cursor_so_the_run_does_not_restart(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)
    acked: dict = {}

    async def _ack(**kwargs):
        acked.update(kwargs)

    monkeypatch.setattr(bus, "ack_processed", _ack)

    async def _cancelled(*args, **kwargs):
        raise CancelledByUser("Owner requested stop")

    monkeypatch.setattr(trigger, "_invoke_runtime", _cancelled)

    await _handle(trigger)

    assert acked.get("agent_id") == "agent_a"
    assert acked.get("channel_id") == "ch_1"


@pytest.mark.asyncio
async def test_stop_is_not_a_delivery_failure(db_client, monkeypatch):
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    record_failure = AsyncMock()
    monkeypatch.setattr(bus, "record_failure", record_failure)
    notify = AsyncMock()
    monkeypatch.setattr(trigger, "_notify_permanent_failure", notify)

    async def _cancelled(*args, **kwargs):
        raise CancelledByUser("Owner requested stop")

    monkeypatch.setattr(trigger, "_invoke_runtime", _cancelled)

    await _handle(trigger)

    record_failure.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_real_error_is_still_a_failure(db_client, monkeypatch):
    """The cancel branch must not swallow genuine faults on its way past."""
    _patch_db_factory(monkeypatch, db_client)
    await _seed_agent(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    trigger = MessageBusTrigger(bus=bus)

    record_failure = AsyncMock()
    monkeypatch.setattr(bus, "record_failure", record_failure)
    monkeypatch.setattr(bus, "get_failure_count", AsyncMock(return_value=1))

    async def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(trigger, "_invoke_runtime", _boom)

    await _handle(trigger)

    record_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_runtime_forwards_cancellation_to_the_runtime(monkeypatch):
    """The token must ride the extra_kwargs seam all the way to the runtime."""
    captured: dict = {}

    async def _run_and_collect(**kwargs):
        captured.update(kwargs)
        return RunCollection(
            output_text="ok", tool_calls=[], raw_items=[], event_id="evt_1",
        )

    client = SimpleNamespace(run_and_collect=AsyncMock(side_effect=_run_and_collect))
    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: client,
    )

    from narranexus.platform.agent_runtime.cancellation import CancellationToken

    token = CancellationToken()
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    await trigger._invoke_runtime(
        agent_id="agent_a",
        sender_agent_id="usr_user_x",
        prompt="p",
        channel_id="ch_1",
        cancellation=token,
    )

    assert captured["cancellation"] is token


@pytest.mark.asyncio
async def test_invoke_runtime_forwards_the_team_safety_net(monkeypatch):
    """The seam must carry what the team lane depends on, checked HERE.

    RE-POINTED 2026-08-17. This used to check `on_plain_text_delivery`; that
    parameter is gone with the auto-post. The hazard it was written for is not:
    every other test in this area replaces `_invoke_runtime` wholesale, so the
    real signature is never executed by them — which is how a rebase once dropped
    a parameter while the call site kept passing it, leaving a `TypeError` on
    every bus message that no test could see.

    What rides the seam now and would fail SILENTLY rather than loudly:

    * `turn_profile` — the team lane's in-turn nudge. If it stops being
      forwarded, nothing raises; a mute team turn simply stops being steered.
    * `include_monologue` — patrol's. Its absence makes patrol mute on NexusPower
      and fine on claude_code, the shape of bug this repo has paid for twice.
    """
    captured: dict = {}

    async def _run_and_collect(**kwargs):
        captured.update(kwargs)
        return RunCollection(
            output_text="ok", tool_calls=[], raw_items=[], event_id="evt_1",
        )

    client = SimpleNamespace(run_and_collect=AsyncMock(side_effect=_run_and_collect))
    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: client,
    )

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)

    await trigger._invoke_runtime(
        agent_id="agent_a", sender_agent_id="usr_user_x", prompt="hello",
        channel_id="ch_1", team_room=True,
    )
    profile = captured.get("turn_profile")
    assert profile is not None, "the team lane lost its in-turn nudge"
    assert profile.expression_nudge is True
    assert profile.narrative_strategy == "full"
    assert profile.framework_override is None

    captured.clear()
    await trigger._invoke_runtime(
        agent_id="agent_a", sender_agent_id="agent_b", prompt="hello",
        channel_id="ch_2",
    )
    assert captured.get("turn_profile") is None

    captured.clear()
    await trigger._invoke_runtime(
        agent_id="agent_a", sender_agent_id="usr_user_x", prompt="hello",
        channel_id="ch_1", team_room=True, include_monologue=True,
    )
    assert captured.get("include_monologue") is True

@pytest.mark.asyncio
async def test_invoke_runtime_works_without_a_deliverer(monkeypatch):
    """Every non-team lane passes None for it, so the default has to hold."""
    captured: dict = {}

    async def _run_and_collect(**kwargs):
        captured.update(kwargs)
        return RunCollection(
            output_text="ok", tool_calls=[], raw_items=[], event_id="evt_1",
        )

    client = SimpleNamespace(run_and_collect=AsyncMock(side_effect=_run_and_collect))
    monkeypatch.setattr(
        "narranexus.platform.agent_runtime.client.get_agent_runtime_client",
        lambda: client,
    )

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    await trigger._invoke_runtime(
        agent_id="agent_a", sender_agent_id="agent_b", prompt="hi", channel_id="ch_1",
    )

    assert captured.get("on_plain_text_delivery") is None
