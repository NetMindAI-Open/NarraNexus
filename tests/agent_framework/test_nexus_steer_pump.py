"""
@file_name: test_nexus_steer_pump.py
@author: Bin Liang
@date: 2026-08-21
@description: NexusAgent._pump_steer_to_stdin — the subprocess steer
transport's write side: a push onto the run's SteerChannel becomes a
{"steer": …} line on the runner's stdin. (The runner-side read is covered
by test_runner_steer; the full loop delivery by the in-process e2e.)
"""

import asyncio
import contextlib
import json

import pytest

from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import NexusAgent
from narranexus.platform.agent_runtime.steer_channel import (
    SteerChannel,
    rendered_injection_payload,
)
from narranexus.platform.schema.steer_schema import SteerInjection


class _FakeStdin:
    def __init__(self):
        self.lines: list[bytes] = []
        self.on_write = lambda: None
        self.closed = False

    def write(self, data: bytes) -> None:
        self.lines.append(data)
        self.on_write()

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


class _FakeProcess:
    def __init__(self):
        self.stdin = _FakeStdin()


class _NeverCancel:
    def requested(self) -> bool:
        return False


class _FakeStdout:
    def __init__(self, lines):
        self._lines = [(ln + "\n").encode("utf-8") for ln in lines] + [b""]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeStderr:
    async def read(self):
        return b""


class _FullFakeProcess:
    def __init__(self, lines):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(lines)
        self.stderr = _FakeStderr()
        self.returncode = 0
        self.pid = -987654  # never a real group; _terminate_group is patched too

    async def wait(self):
        return 0


class _FakePool:
    enabled = False

    def __init__(self, proc):
        self._proc = proc

    async def spawn(self, prewarm=False):
        return self._proc

    async def acquire(self):
        return self._proc


@pytest.mark.asyncio
async def test_run_subprocess_intercepts_steer_consumed_and_does_not_yield_it(monkeypatch):
    # Production team turns run in a SUBPROCESS. The consumption interception on
    # THIS path must call deliver_consumed and NOT surface the line as an event.
    import narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent as na
    import json as _json

    proc = _FullFakeProcess([
        _json.dumps({"steer_consumed": ["m1", "m2"]}),
        _json.dumps({"event": {"type": "text_delta", "seq": 1, "track": "ui",
                               "payload": {"text": "hi"}}}),
    ])
    pool = _FakePool(proc)
    monkeypatch.setattr(na._WarmRunnerPool, "shared", classmethod(lambda cls: pool))
    monkeypatch.setattr(na, "_terminate_group", lambda pid: None)

    channel = SteerChannel()
    channel.remember("m1", "2026-08-24T00:00:00+00:00")
    channel.remember("m2", "2026-08-24T00:00:01+00:00")
    seen: list = []

    async def _on_consumed(ids, latest):
        seen.append((list(ids), latest))

    channel.on_consumed = _on_consumed

    agent = NexusAgent(working_path="/tmp")
    events = []
    async for e in agent._run_subprocess(
        {"thread_id": "t", "messages": [], "options": {}}, _NeverCancel(), channel
    ):
        events.append(e)

    # deliver_consumed fired once with the ids + the newest remembered created_at;
    # the steer_consumed line was intercepted, never yielded as an event.
    assert seen == [(["m1", "m2"], "2026-08-24T00:00:01+00:00")]
    assert all("steer_consumed" not in str(e) for e in events)


@pytest.mark.asyncio
async def test_pump_carries_the_steer_id_through_the_stdin_frame():
    # The subprocess path (production) forwards the WHOLE steer msg, so the
    # runner-side inlet gets `_steer_id` and can report consumption back. If the
    # pump ever narrows the frame to role/content, consumption silently breaks.
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    channel = SteerChannel()
    inj = SteerInjection(run_id="r1", msg_id="m9", role="user",
                         content="x", sender_id="a", source="team")
    written = asyncio.Event()
    proc.stdin.on_write = written.set
    pump = asyncio.create_task(agent._pump_steer_to_stdin(proc, channel, _NeverCancel()))
    try:
        await channel.push(inj)
        await asyncio.wait_for(written.wait(), timeout=2)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
    frame = json.loads(proc.stdin.lines[0].decode("utf-8"))
    assert frame["steer"]["_steer_id"] == "m9"


@pytest.mark.asyncio
async def test_push_becomes_a_steer_line_on_stdin():
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    channel = SteerChannel()
    inj = SteerInjection(run_id="r1", msg_id="m1", role="user",
                         content="reconsider", sender_id="bob", source="team")

    written = asyncio.Event()
    proc.stdin.on_write = written.set  # fire when the pump writes a line
    pump = asyncio.create_task(
        agent._pump_steer_to_stdin(proc, channel, _NeverCancel())
    )
    try:
        await channel.push(inj)
        await asyncio.wait_for(written.wait(), timeout=2)
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump

    assert proc.stdin.lines, "the pump must write the pushed injection to stdin"
    frame = json.loads(proc.stdin.lines[0].decode("utf-8"))
    # render_injection now stamps a random per-render nonce into the block
    # delimiter (anti-forge, see test_steer_channel), so it is no longer a pure
    # function — assert on structure, not on a second render's exact bytes: the
    # frame carries the SAME rendered dict the channel enqueued (role, leading
    # tag, and the pushed content preserved byte-for-byte inside its block).
    steer = frame["steer"]
    assert steer["role"] == "user"
    assert steer["content"].startswith("[teammate bob just posted to the room]")
    assert rendered_injection_payload(steer["content"]) == "reconsider"


@pytest.mark.asyncio
async def test_non_steerable_run_closes_stdin_after_the_request():
    # steer_channel=None is the default path: the request is written and stdin
    # is CLOSED at once (the runner's reader then EOFs) — the "zero behaviour
    # change" invariant the PR relies on.
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    pump = await agent._open_steer_transport(
        proc, {"thread_id": "t", "messages": [], "options": {}}, None, _NeverCancel()
    )
    assert pump is None
    assert proc.stdin.closed is True
    assert proc.stdin.lines  # the request line was written


@pytest.mark.asyncio
async def test_steerable_run_keeps_stdin_open_for_the_pump():
    # A steerable run must NOT close stdin after the request — the pump needs it
    # open to write steer lines for the life of the turn.
    agent = NexusAgent(working_path="/tmp")
    proc = _FakeProcess()
    channel = SteerChannel()
    pump = await agent._open_steer_transport(
        proc, {"thread_id": "t", "messages": [], "options": {}}, channel, _NeverCancel()
    )
    try:
        assert pump is not None
        assert proc.stdin.closed is False  # kept open for the pump
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
