"""
@file_name: test_codex_cli_stdout_line_limit.py
@date: 2026-07-09
@description: Regressions for the codex CLI stdout StreamReader line limit.

Companion to the 2026-07-08 multimodal-large-file incident. The
``xyz_codex_cli_sdk`` wrapper spawns codex CLI via
``asyncio.create_subprocess_exec``. Without an explicit ``limit=``
kwarg it inherits ``asyncio.streams._DEFAULT_LIMIT = 65536`` (64 KiB),
half of aiohttp's 128 KiB. A ``tool_result`` NDJSON line carrying a
base64 image can hit 150-400 KiB, so a codex-framework agent reading
even a modest image blew up at ``StreamReader.readline`` before the
event ever reached the HTTP hop.

Two locks:
1. ``_STDOUT_LINE_LIMIT`` is set to 50 MiB and travels to
   ``create_subprocess_exec`` as the ``limit`` kwarg.
2. A raw ``StreamReader(limit=_STDOUT_LINE_LIMIT)`` can ``readline`` a
   200 KiB line — direct simulation of the failure the incident hit.
"""
from __future__ import annotations

import asyncio

import pytest

from xyz_agent_context.agent_framework import xyz_codex_cli_sdk as codex_mod
from xyz_agent_context.agent_framework.xyz_codex_cli_sdk import (
    CodexSDK,
    _STDOUT_LINE_LIMIT,
)


def test_stdout_line_limit_matches_sdk_max_buffer():
    """Sanity: the codex wrapper's line ceiling is aligned with the
    Claude SDK's ``max_buffer_size`` (50 MiB). Drifting them apart
    would recreate a mismatched-ceiling class of bug."""
    assert _STDOUT_LINE_LIMIT == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_stream_reader_at_limit_reads_200_kib_line():
    """Direct simulation of the incident scenario. A
    ``StreamReader`` at the wrapper's real limit MUST be able to
    ``readline`` a 200 KiB line (well past the asyncio 64 KiB default
    that caused the crash) without raising."""
    reader = asyncio.StreamReader(limit=_STDOUT_LINE_LIMIT)
    payload = b"x" * (200 * 1024) + b"\n"
    reader.feed_data(payload)
    reader.feed_eof()
    line = await reader.readline()
    assert len(line) == 200 * 1024 + 1  # payload + newline
    assert line.endswith(b"\n")


@pytest.mark.asyncio
async def test_stream_reader_at_default_limit_would_crash():
    """Sanity control: the SAME 200 KiB line hitting a StreamReader
    at asyncio's default 64 KiB limit MUST raise, otherwise we're
    not actually testing the incident condition. Locks in that the
    fix's ceiling change is what matters — not some other reason
    the test above happens to pass."""
    reader = asyncio.StreamReader(limit=65536)  # asyncio._DEFAULT_LIMIT
    payload = b"x" * (200 * 1024) + b"\n"
    reader.feed_data(payload)
    reader.feed_eof()
    # asyncio's exact message differs by version — 3.13 raises
    # "Separator is found, but chunk is longer than limit" when the
    # data is already buffered; older Pythons raise "Separator is not
    # found, and chunk exceed the limit". Both match "longer than the
    # limit" semantically; the shared substring is "limit".
    with pytest.raises(ValueError, match="limit"):
        await reader.readline()


@pytest.mark.asyncio
async def test_agent_loop_passes_limit_kwarg_to_subprocess_spawn(monkeypatch):
    """Regression: ``CodexSDK.agent_loop`` must pass ``limit`` to
    ``asyncio.create_subprocess_exec``. Captures the kwargs the
    wrapper hands the subprocess API by intercepting the spawn call
    with a sentinel that raises after recording. Any drift that drops
    ``limit=`` breaks this test."""
    captured: dict = {}

    class _StopHere(Exception):
        pass

    async def _fake_spawn(*args, **kwargs):
        captured["kwargs"] = kwargs
        raise _StopHere("stop after capturing spawn kwargs")

    # ``shutil.which`` gate has to be satisfied first; then a benign
    # codex_home dir; then we intercept the spawn.
    monkeypatch.setattr(codex_mod.shutil, "which", lambda name: "/fake/codex")
    monkeypatch.setattr(codex_mod.asyncio, "create_subprocess_exec", _fake_spawn)

    sdk = CodexSDK("/tmp")
    # Prime a minimum-viable messages list so _build_system_prompt_and_user_msg
    # produces a real prompt string.
    messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "hi"},
    ]

    with pytest.raises(_StopHere):
        async for _ in sdk.agent_loop(messages, {}):
            pass

    assert "limit" in captured["kwargs"], (
        "CodexSDK.agent_loop dropped the `limit=` kwarg to "
        "create_subprocess_exec — this is the 2026-07-09 regression "
        "guard against the LineTooLong-class bug at 64 KiB."
    )
    assert captured["kwargs"]["limit"] == _STDOUT_LINE_LIMIT
