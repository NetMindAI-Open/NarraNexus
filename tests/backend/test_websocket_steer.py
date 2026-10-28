"""
@file_name: test_websocket_steer.py
@author: Bin Liang
@date: 2026-08-24
@description: Owner mid-run steering over the chat WebSocket.

`_listen_for_control` (the WS control listener) gains a `steer` action: a
follow-up the owner sends WHILE a run is in flight is pushed into that run's
SteerChannel and folds into the same turn, instead of starting a fresh run. Stop
/ force_stop still cancel. Consumption is relayed back out-of-band.

Delete the `steer` branch and the first test goes red — the follow-up would fall
through and never reach the loop.
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

import backend.routes.websocket as ws_mod
from narranexus.platform.agent_runtime.cancellation import CancellationToken
from narranexus.platform.agent_runtime.steer_channel import SteerChannel
from narranexus_plugins.frameworks_nexus_power.core.contracts.model import STEER_ID_KEY


class _FakeWS:
    """Feeds scripted control frames, then a normal client disconnect — the
    same shape `_listen_for_control` sees from a real socket."""

    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent: list = []

    async def receive_json(self):
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect(code=1000, reason="")

    async def send_json(self, obj):
        self.sent.append(obj)


@pytest.mark.asyncio
async def test_steer_message_is_pushed_into_the_run_and_acked():
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ch.run_id = "run-1"
    ws = _FakeWS([
        {"action": "steer", "input_content": "one more thing", "client_msg_id": "m1"},
    ])
    cancel = CancellationToken()

    await ws_mod._listen_for_control(ws, cancel, ch, user_id="u", steerable=True)

    # The follow-up reached the run's inlet queue, rendered as a provider message
    # and stamped with the client's id so consumption can be correlated back.
    msg = ch.queue.get_nowait()
    assert "one more thing" in msg["content"]
    assert msg[STEER_ID_KEY] == "m1"
    # A steer NEVER cancels the run (that is stop's job).
    assert not cancel.is_cancelled
    # The client is told the follow-up is queued so it can show the bubble.
    assert {"type": "steer_queued", "client_msg_id": "m1"} in ws.sent


@pytest.mark.asyncio
async def test_steer_mints_an_id_when_the_client_omits_one():
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([{"action": "steer", "input_content": "hi"}])
    await ws_mod._listen_for_control(ws, CancellationToken(), ch, user_id="u", steerable=True)
    msg = ch.queue.get_nowait()
    assert msg[STEER_ID_KEY]  # a non-empty id was minted
    acked = [s for s in ws.sent if s.get("type") == "steer_queued"]
    assert acked and acked[0]["client_msg_id"] == msg[STEER_ID_KEY]


@pytest.mark.asyncio
async def test_blank_or_non_string_steer_is_ignored():
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([
        {"action": "steer", "input_content": "   "},
        {"action": "steer", "input_content": None},
        {"action": "steer"},  # missing entirely
    ])
    await ws_mod._listen_for_control(ws, CancellationToken(), ch, user_id="u")
    assert ch.queue.empty()  # nothing bad reached the run


@pytest.mark.asyncio
async def test_oversize_steer_is_rejected_not_truncated():
    from narranexus.platform.repository.steer_inbox_repository import MAX_CONTENT_BYTES
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([])
    big = "x" * (MAX_CONTENT_BYTES + 1)
    await ws_mod._route_steer(ws, ch, "u", {"input_content": big, "client_msg_id": "m1"}, steerable=True)
    assert ch.queue.empty()  # NOT pushed
    # An explicit rejection frame (never a silent drop, never a queued ack) — else
    # the client's optimistic bubble hangs forever.
    assert {"type": "steer_rejected", "client_msg_id": "m1", "reason": "too_large"} in ws.sent
    assert not any(s.get("type") == "steer_queued" for s in ws.sent)


@pytest.mark.asyncio
async def test_backlog_over_the_cap_is_rejected():
    from narranexus.platform.repository.steer_inbox_repository import MAX_UNCONSUMED_PER_RUN
    ch = SteerChannel(agent_id="a", channel_id="chat")
    for _ in range(MAX_UNCONSUMED_PER_RUN):  # fill to the cap
        ch.queue.put_nowait({"role": "user", "content": "x"})
    ws = _FakeWS([])
    await ws_mod._route_steer(ws, ch, "u", {"input_content": "one more", "client_msg_id": "m2"}, steerable=True)
    assert ch.queue.qsize() == MAX_UNCONSUMED_PER_RUN  # nothing added past the cap
    assert {"type": "steer_rejected", "client_msg_id": "m2", "reason": "too_many_pending"} in ws.sent


@pytest.mark.asyncio
async def test_steer_rejected_when_framework_cannot_steer():
    # A claude_code / codex slot never drains a steer, so accepting it would hang
    # the bubble forever. The run advertises steerable=False and the listener
    # rejects rather than no-op silently.
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([])
    await ws_mod._route_steer(ws, ch, "u", {"input_content": "hi", "client_msg_id": "m3"}, steerable=False)
    assert ch.queue.empty()
    assert {"type": "steer_rejected", "client_msg_id": "m3", "reason": "framework_no_steering"} in ws.sent
    assert not any(s.get("type") == "steer_queued" for s in ws.sent)


@pytest.mark.asyncio
async def test_overlong_client_msg_id_is_replaced_not_smuggled():
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([])
    await ws_mod._route_steer(ws, ch, "u", {"input_content": "hi", "client_msg_id": "z" * 5000}, steerable=True)
    msg = ch.queue.get_nowait()
    assert msg[STEER_ID_KEY] != "z" * 5000  # the unbounded id was NOT used
    assert 0 < len(msg[STEER_ID_KEY]) <= 128


@pytest.mark.asyncio
async def test_stop_still_cancels_and_steer_does_not():
    ch = SteerChannel(agent_id="a", channel_id="chat")
    ws = _FakeWS([{"action": "stop"}])
    cancel = CancellationToken()
    await ws_mod._listen_for_control(ws, cancel, ch, user_id="u")
    assert cancel.is_cancelled


@pytest.mark.asyncio
async def test_consumed_ids_relay_shape():
    # The handler wires steer_channel.on_consumed to emit a steer_consumed frame;
    # this pins the contract that wiring depends on — deliver_consumed forwards
    # the exact ids the loop reported, which the client matches to its bubbles.
    ws = _FakeWS([])
    ch = SteerChannel(agent_id="a", channel_id="chat")

    async def relay(ids, _latest):
        await ws.send_json({"type": "steer_consumed", "ids": ids})

    ch.on_consumed = relay
    await ch.deliver_consumed(["m1", "m2"])
    assert {"type": "steer_consumed", "ids": ["m1", "m2"]} in ws.sent


# --- steerability probe: driver.capabilities(), not a hardcoded framework name ---

class _FakeDriver:
    def __init__(self, caps):
        self._caps = caps
    def capabilities(self):
        return set(self._caps)


class _Identity:
    def __init__(self, framework):
        self.framework = framework


@pytest.mark.asyncio
async def test_steerability_asks_the_actual_driver_not_the_framework_name(monkeypatch):
    import narranexus.platform.agent_framework.providers.model_identity as mid
    import narranexus.platform.agent_framework.loop.driver as drv

    captured = {}

    async def _fake_identity(agent_id, db):
        return _Identity("nexus_power")

    def _fake_get_driver(framework, *, executor_url=None, working_path=None):
        captured["executor_url"] = executor_url
        # Simulate the REMOTE driver on this branch: framework is nexus_power but
        # the remote transport does not (yet) declare steering → base contract.
        # A framework-name check would wrongly return True here.
        return _FakeDriver(set() if executor_url else {"steering"})

    async def _fake_db():
        return object()

    monkeypatch.setattr(mid, "resolve_agent_model_identity", _fake_identity)
    monkeypatch.setattr(drv, "get_agent_loop_driver", _fake_get_driver)
    monkeypatch.setattr(ws_mod, "get_db_client", _fake_db)

    # Cloud: broker configured → remote driver → this branch's remote caps have no
    # steering → NOT steerable (even for nexus_power). The old name-based gate got
    # this exactly wrong.
    monkeypatch.setenv("BROKER_URL", "http://broker:8030")
    assert await ws_mod._resolve_run_steerable("a") is False
    assert captured["executor_url"]  # a (placeholder) URL selected the remote driver

    # Local: no broker → in-process driver → declares steering → steerable.
    monkeypatch.delenv("BROKER_URL", raising=False)
    monkeypatch.delenv("AGENT_EXECUTOR_URL", raising=False)
    assert await ws_mod._resolve_run_steerable("a") is True
    assert captured["executor_url"] is None
