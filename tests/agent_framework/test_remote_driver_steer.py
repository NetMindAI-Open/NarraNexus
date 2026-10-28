"""
@file_name: test_remote_driver_steer.py
@date: 2026-08-24
@description: The orchestrator half of cloud live-steering.

RemoteAgentLoopDriver now (a) declares the `steering` capability, (b) forwards
the executor's `{"steer_consumed": …}` frames to the run's real SteerChannel via
`_handle_frame`, and (c) pumps each queued injection to the executor's `/steer`
endpoint via `_pump_steer`. These unit-pin those three; the full HTTP round-trip
is left to dev-EC2 real-executor verification (the transport is load-bearing and
fake-model e2e cannot exercise a real runner subprocess).

Delete `_handle_frame`'s steer_consumed branch and consumption never reaches the
producer's cursor (a steered message re-fires as a fresh turn); delete the
`/steer` POST in `_pump_steer` and a mid-run injection is silently dropped.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from narranexus.platform.agent_framework.loop.remote_driver import RemoteAgentLoopDriver
from narranexus.platform.agent_runtime.executor_protocol import build_steer_request


def _driver():
    return RemoteAgentLoopDriver(
        framework="nexus_power", working_path="/tmp",
        executor_url="http://exec:8020",
    )


def test_capabilities_declares_steering():
    assert "steering" in _driver().capabilities()


def test_steer_url_is_derived_from_the_executor_base():
    d = _driver()
    assert d._url == "http://exec:8020/agent-loop"
    assert d._steer_url == "http://exec:8020/steer"


# ------------------------------------------------------------------ _handle_frame


class _RecordingChannel:
    def __init__(self):
        self.consumed = []

    async def deliver_consumed(self, ids):
        self.consumed.append(list(ids))


@pytest.mark.asyncio
async def test_handle_frame_yields_events_and_forwards_consumed():
    d = _driver()
    ch = _RecordingChannel()

    # An event frame → the event dict (to be yielded), nothing consumed.
    ev = await d._handle_frame(b'{"event": {"type": "text", "data": "hi"}}', ch)
    assert ev == {"type": "text", "data": "hi"}
    assert ch.consumed == []

    # A steer_consumed frame → None (swallowed), forwarded to the real channel so
    # the producer advances its cursor on consumption.
    none = await d._handle_frame(b'{"steer_consumed": ["r-7", "r-8"]}', ch)
    assert none is None
    assert ch.consumed == [["r-7", "r-8"]]


@pytest.mark.asyncio
async def test_handle_frame_raises_on_an_error_frame():
    d = _driver()
    with pytest.raises(RuntimeError, match="BadThing: boom"):
        await d._handle_frame(b'{"error": {"type": "BadThing", "message": "boom"}}', None)


@pytest.mark.asyncio
async def test_handle_frame_tolerates_a_consumed_frame_with_no_channel():
    # Defensive: a consumed frame with steer_channel=None is dropped, not crashed.
    assert await _driver()._handle_frame(b'{"steer_consumed": ["x"]}', None) is None


# ------------------------------------------------------------------ _pump_steer


class _Token:
    def __init__(self):
        self._set = False

    def trip(self):
        self._set = True

    def is_set(self):
        return self._set


class _Resp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, status=200, raise_exc=None):
        self.posts = []
        self._status = status
        self._raise = raise_exc

    def post(self, url, json, timeout=None):
        self.posts.append((url, json))
        if self._raise is not None:
            raise self._raise
        return _Resp(self._status)


class _Chan:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()


async def _until(pred, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


def _cancel(token):
    from narranexus.platform.agent_framework.loop.cancellation_view import CancellationView
    return CancellationView(token)


@pytest.mark.asyncio
async def test_pump_posts_each_injection_to_steer_then_stops_on_cancel():
    d = _driver()
    ch = _Chan()
    m1 = {"role": "user", "content": "one", "_steer_id": "s1"}
    m2 = {"role": "user", "content": "two", "_steer_id": "s2"}
    ch.queue.put_nowait(m1)
    ch.queue.put_nowait(m2)
    session = _FakeSession(status=200)
    token = _Token()

    task = asyncio.create_task(d._pump_steer(session, "run-9", ch, _cancel(token)))
    await _until(lambda: len(session.posts) == 2)
    token.trip()  # turn ended
    await asyncio.wait_for(task, 1.0)

    # Each drained injection was POSTed to /steer under the run id, in order, in
    # the {"run_id", "steer"} envelope the executor unwraps.
    assert session.posts == [
        (d._steer_url, build_steer_request(run_id="run-9", steer_msg=m1)),
        (d._steer_url, build_steer_request(run_id="run-9", steer_msg=m2)),
    ]


@pytest.mark.asyncio
async def test_pump_keeps_going_on_a_transport_error_and_never_raises():
    # A /steer POST rides its OWN short-lived connection, separate from the
    # /agent-loop stream — so a connection blip there does NOT mean the run ended.
    # The pump treats it as transient: logs, keeps draining (so one blip can't
    # kill every later steer), and never raises into the turn. It stops only on
    # cancellation (turn end).
    d = _driver()
    ch = _Chan()
    ch.queue.put_nowait({"role": "user", "content": "a"})
    ch.queue.put_nowait({"role": "user", "content": "b"})
    session = _FakeSession(raise_exc=aiohttp.ClientConnectionError("down"))
    token = _Token()

    task = asyncio.create_task(d._pump_steer(session, "run-x", ch, _cancel(token)))
    await _until(lambda: len(session.posts) == 2)  # BOTH attempted despite the error
    token.trip()  # turn ends → pump settles
    await asyncio.wait_for(task, 1.0)
    assert len(session.posts) == 2


@pytest.mark.asyncio
async def test_pump_keeps_draining_after_a_404():
    # A per-run 404 (run just ended) must NOT wedge the queue — the pump logs and
    # keeps draining so a cancel can still settle it.
    d = _driver()
    ch = _Chan()
    ch.queue.put_nowait({"role": "user", "content": "a"})
    ch.queue.put_nowait({"role": "user", "content": "b"})
    session = _FakeSession(status=404)
    token = _Token()

    task = asyncio.create_task(d._pump_steer(session, "run-z", ch, _cancel(token)))
    await _until(lambda: len(session.posts) == 2)  # both drained despite 404
    token.trip()
    await asyncio.wait_for(task, 1.0)
    assert len(session.posts) == 2


# ------------------------------------------------- agent_loop framework gate


class _StreamResp:
    def __init__(self, lines):
        self._lines = lines
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    @property
    def content(self):
        return self

    async def iter_any(self):
        for ln in self._lines:
            yield ln


class _LoopSession:
    """Captures every POST; serves the /agent-loop one as a short NDJSON stream,
    the /steer ones as 200s."""

    def __init__(self, lines):
        self.posts = []
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def post(self, url, json, timeout=None):
        self.posts.append((url, json))
        return _StreamResp(self._lines) if url.endswith("/agent-loop") else _Resp(200)


@pytest.mark.asyncio
async def test_agent_loop_carries_run_id_only_for_a_steer_capable_framework(monkeypatch):
    # The framework gate, at the wire: a steer channel is supplied for BOTH, but
    # only nexus_power (whose executor driver drains steering) gets a run_id in
    # the /agent-loop body — claude_code must not, or the executor would register
    # an inbound channel nothing reads. Pins the `steerable = channel and
    # "steering" in capabilities()` gate end to end.
    done = b'{"event": {"type": "done"}}\n'
    for fw, expect_run_id in (("nexus_power", True), ("claude_code", False)):
        session = _LoopSession([done])
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
        d = RemoteAgentLoopDriver(framework=fw, working_path="/tmp", executor_url="http://exec:8020")
        _ = [e async for e in d.agent_loop(messages=[], mcp_servers={}, steering=_Chan())]
        body = next(p[1] for p in session.posts if p[0].endswith("/agent-loop"))
        assert ("run_id" in body) is expect_run_id, f"{fw}: run_id presence wrong"
