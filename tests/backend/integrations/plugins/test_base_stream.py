"""
@file_name: test_base_stream.py
@author: NarraNexus
@date: 2026-08-28
@description: stream_subprocess must reap the package-manager process on ANY
              exit path — including a client disconnect that abandons the
              generator mid-stream (I3). Delete the ``finally`` kill/reap block
              and test_stream_subprocess_kills_process_when_abandoned goes red.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.integrations.plugins._installers import base


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self.waited = False
        self.stdout = self._lines()

    async def _lines(self):
        yield b"line1\n"
        await asyncio.Event().wait()  # hang after the first line

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_stream_subprocess_kills_process_when_abandoned(monkeypatch):
    proc = _FakeProc()

    async def _fake_exec(*_cmd, **_kwargs):
        return proc

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", _fake_exec)

    gen = base.stream_subprocess(["npm", "install", "whatever"])
    line = await gen.__anext__()
    assert line == "line1"
    assert proc.killed is False  # still running

    await gen.aclose()  # client disconnected → generator abandoned

    assert proc.killed is True
    assert proc.waited is True


@pytest.mark.asyncio
async def test_stream_subprocess_does_not_kill_a_finished_process(monkeypatch):
    """On the normal path the process already exited — the finally must not
    kill a corpse (returncode is already set)."""

    class _Done:
        def __init__(self):
            self.returncode = None
            self.killed = False
            self.stdout = self._lines()

        async def _lines(self):
            yield b"done\n"

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = 0
            return 0

    proc = _Done()

    async def _fake_exec(*_cmd, **_kwargs):
        return proc

    monkeypatch.setattr(base.asyncio, "create_subprocess_exec", _fake_exec)

    lines = [line async for line in base.stream_subprocess(["pip", "install", "x"])]
    assert lines == ["done"]
    assert proc.killed is False
