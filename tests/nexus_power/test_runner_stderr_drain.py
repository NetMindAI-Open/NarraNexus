"""
@file_name: test_runner_stderr_drain.py
@author: Bin Liang
@date: 2026-09-06
@description: Regression for the silent NexusPower hang found on a fresh install: the runner's stderr pipe was read only after exit, so a chatty child (litellm debug, import-time registrations) filled the 64 KB kernel buffer mid-turn and blocked while the parent waited on stdout. The adapter now drains stderr from spawn and keeps only a bounded tail.
"""
from __future__ import annotations

import asyncio
import sys

import pytest

from narranexus_plugins.frameworks_nexus_power.adapter import nexus_agent as mod

CHATTY = (
    "import sys\n"
    "sys.stderr.write('x' * 300_000)\n"  # far beyond the pipe buffer
    "sys.stderr.write('LAST LINE\\n')\n"
    "sys.stderr.flush()\n"
    "sys.stdout.write('{\"event\": 1}\\n')\n"
    "sys.stdout.flush()\n"
)


@pytest.mark.asyncio
async def test_child_that_floods_stderr_still_finishes_its_stdout_line_and_tail_is_bounded():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", CHATTY, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    mod.start_stderr_drain(proc)
    line = await asyncio.wait_for(proc.stdout.readline(), timeout=20)  # would hang forever without the drain
    assert line.strip() == b'{"event": 1}'
    await asyncio.wait_for(proc.wait(), timeout=20)
    tail = await mod.stderr_tail(proc)
    assert tail.endswith("LAST LINE\n") and len(tail) <= mod._STDERR_TAIL_BYTES


@pytest.mark.asyncio
async def test_spawn_attaches_the_drain(monkeypatch):
    seen = {}

    class _Proc:
        pid = 4242
        stderr = None
        returncode = None

    async def fake_exec(*args, **kwargs):
        seen["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)
    proc = await mod._WarmRunnerPool().spawn(prewarm=False)
    assert seen["kwargs"]["stderr"] == asyncio.subprocess.PIPE
    task = getattr(proc, "_nx_stderr_tail")
    assert await task == b""  # no stderr stream on the fake: the drain settles immediately
    assert await mod.stderr_tail(proc) == ""
