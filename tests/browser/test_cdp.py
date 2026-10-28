"""
@file_name: test_cdp.py
@author:
@date: 2026-09-22
@description: Tests for the CDP plumbing — request/response correlation,
the screencast ack loop, and input event translation.

Three things here were learned the hard way during the 2026-09-21 spike and
are pinned so they cannot regress:

* **Every screencast frame must be acked.** Without an ack Chromium sends
  exactly one frame and then stops, which looks identical to "the page is
  static" and cost an afternoon to diagnose.
* **A response can only arrive while something is reading the socket.**
  Awaiting a CDP call before starting the reader deadlocks — that bug was
  written, hit, and fixed during the spike.
* **Screencast only emits on visual change.** Zero fps on a still page is
  correct behaviour, not a stall, and nothing downstream may treat it as one.

No browser and no sockets: the transport is a fake.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from narranexus.platform.browser._browser_impl.cdp import (
    CdpSession,
    input_events_for,
)


class FakeSocket:
    """A CDP socket that answers commands and can push events."""

    def __init__(self):
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False
        self.auto_reply = True

    async def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        if self.auto_reply and "id" in msg:
            await self._inbox.put(json.dumps({"id": msg["id"], "result": {"ok": True}}))

    async def push_event(self, method: str, params: dict) -> None:
        await self._inbox.put(json.dumps({"method": method, "params": params}))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if self.closed and self._inbox.empty():
            raise StopAsyncIteration
        return await self._inbox.get()

    async def close(self) -> None:
        self.closed = True


async def started_session() -> tuple[CdpSession, FakeSocket]:
    sock = FakeSocket()
    session = CdpSession(socket=sock)
    await session.start()
    return session, sock


# ── request / response correlation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_returns_the_matching_result():
    session, _sock = await started_session()
    try:
        result = await session.call("Page.enable")
        assert result == {"ok": True}
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_calls_do_not_deadlock_before_the_reader_is_running():
    """The spike bug: awaiting a call before the read loop exists means the
    response is never dequeued and the coroutine hangs forever."""
    session, _sock = await started_session()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(session.call("A"), session.call("B"), session.call("C")),
            timeout=2,
        )
        assert len(results) == 3
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_ids_are_unique_per_call():
    session, sock = await started_session()
    try:
        await asyncio.gather(session.call("A"), session.call("B"))
        ids = [m["id"] for m in sock.sent]
        assert len(ids) == len(set(ids))
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_error_response_raises_with_the_protocol_message():
    sock = FakeSocket()
    sock.auto_reply = False
    session = CdpSession(socket=sock)
    await session.start()
    try:
        call = asyncio.create_task(session.call("Bad.method"))
        await asyncio.sleep(0)
        sent_id = sock.sent[0]["id"]
        await sock._inbox.put(  # noqa: SLF001
            json.dumps({"id": sent_id, "error": {"code": -32601, "message": "not found"}})
        )
        with pytest.raises(RuntimeError, match="not found"):
            await asyncio.wait_for(call, timeout=2)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_pending_calls_fail_when_the_socket_closes():
    """A dropped connection must fail the waiters, not leave an agent turn
    blocked on a promise nobody can ever resolve."""
    sock = FakeSocket()
    sock.auto_reply = False
    session = CdpSession(socket=sock)
    await session.start()
    call = asyncio.create_task(session.call("Page.enable"))
    await asyncio.sleep(0)
    await session.close()
    with pytest.raises(Exception):
        await asyncio.wait_for(call, timeout=2)


# ── screencast ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_frame_is_acked():
    """Without the ack Chromium sends one frame and stops (spike finding)."""
    session, sock = await started_session()
    got: list[bytes] = []
    try:
        await session.start_screencast(on_frame=lambda data, meta: got.append(data))
        for i in range(3):
            await sock.push_event(
                "Page.screencastFrame", {"data": f"frame{i}", "sessionId": i, "metadata": {}}
            )
        await asyncio.sleep(0.05)

        acks = [m for m in sock.sent if m.get("method") == "Page.screencastFrameAck"]
        assert len(acks) == 3
        assert [a["params"]["sessionId"] for a in acks] == [0, 1, 2]
        assert len(got) == 3
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_still_page_producing_no_frames_is_not_an_error():
    """Screencast only emits on visual change; silence is correct."""
    session, _sock = await started_session()
    try:
        await session.start_screencast(on_frame=lambda *_: None)
        await asyncio.sleep(0.05)
        assert session.frames_seen == 0
        assert session.is_open is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_screencast_preserves_maximum_panel_viewport_resolution():
    session, sock = await started_session()
    try:
        await session.start_screencast(on_frame=lambda *_: None)
        message = next(item for item in sock.sent if item.get("method") == "Page.startScreencast")
        assert message["params"]["maxWidth"] == 1920
        assert message["params"]["maxHeight"] == 1440
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_throwing_frame_handler_does_not_stop_the_stream():
    """One bad subscriber must not end the session for everyone."""
    session, sock = await started_session()
    calls = {"n": 0}

    def handler(_data, _meta):
        calls["n"] += 1
        raise ValueError("subscriber bug")

    try:
        await session.start_screencast(on_frame=handler)
        await sock.push_event("Page.screencastFrame", {"data": "a", "sessionId": 1})
        await sock.push_event("Page.screencastFrame", {"data": "b", "sessionId": 2})
        await asyncio.sleep(0.05)
        assert calls["n"] == 2
        assert session.is_open is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stop_screencast_stops_delivering_frames():
    session, sock = await started_session()
    got: list[bytes] = []
    try:
        await session.start_screencast(on_frame=lambda d, _m: got.append(d))
        await session.stop_screencast()
        await sock.push_event("Page.screencastFrame", {"data": "late", "sessionId": 9})
        await asyncio.sleep(0.05)
        assert got == []
    finally:
        await session.close()


# ── input translation ────────────────────────────────────────────────────────


def test_mouse_press_maps_to_a_cdp_command():
    cmds = input_events_for({"kind": "mouse", "type": "mousePressed", "x": 10, "y": 20})
    assert cmds[0][0] == "Input.dispatchMouseEvent"
    assert cmds[0][1]["x"] == 10 and cmds[0][1]["y"] == 20
    assert cmds[0][1]["button"] == "left"
    assert cmds[0][1]["clickCount"] == 1


def test_wheel_maps_to_mouse_wheel_with_deltas():
    cmds = input_events_for({"kind": "wheel", "x": 1, "y": 2, "deltaX": 0, "deltaY": -120})
    assert cmds[0][1]["type"] == "mouseWheel"
    assert cmds[0][1]["deltaY"] == -120


def test_printable_key_sends_text_so_the_page_receives_a_character():
    """keyDown alone does not type; without `text` the field stays empty."""
    cmds = input_events_for({"kind": "key", "type": "keyDown", "key": "a"})
    assert cmds[0][1]["text"] == "a"


def test_non_printable_key_sends_no_text():
    cmds = input_events_for({"kind": "key", "type": "keyDown", "key": "Tab"})
    assert cmds[0][1].get("text", "") == ""


def test_enter_keydown_includes_carriage_return_for_native_form_submission():
    cmds = input_events_for({"kind": "key", "type": "keyDown", "key": "Enter"})
    assert cmds[0][1]["text"] == "\r"


@pytest.mark.parametrize("event", [
    {"type": "keyUp"}, {"type": "rawKeyDown"},
    {"type": "keyDown", "modifiers": 2},
    {"type": "keyDown", "modifiers": 4},
])
def test_enter_release_and_shortcuts_do_not_insert_text(event):
    cmds = input_events_for({"kind": "key", "key": "Enter", **event})
    assert cmds[0][1]["text"] == ""


def test_unknown_event_kind_is_dropped_not_forwarded():
    """Input arrives from the frontend over a socket; anything unrecognised is
    untrusted and must not be reflected into CDP."""
    assert input_events_for({"kind": "exec", "cmd": "rm -rf /"}) == []


def test_missing_coordinates_are_dropped():
    assert input_events_for({"kind": "mouse", "type": "mousePressed"}) == []


def test_non_numeric_coordinates_are_dropped():
    assert input_events_for({"kind": "mouse", "type": "mousePressed", "x": "10", "y": {}}) == []


def test_unknown_mouse_type_is_dropped():
    assert input_events_for({"kind": "mouse", "type": "mouseTeleport", "x": 1, "y": 2}) == []
