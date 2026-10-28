"""
@file_name: test_background_run_fast_mode.py
@date: 2026-08-14
@description: BackgroundRun.drive forwards fast_mode to AgentRuntime.run.

drive() is a pure passthrough for the flag — policy lives in
AgentRuntime._resolve_turn_profile (its own test file). Locks:
  * drive(fast_mode=True)  -> runtime.run receives fast_mode=True
  * drive() default        -> runtime.run receives fast_mode=False
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

import narranexus.platform.agent_runtime.agent_runtime as agent_runtime_module
import narranexus.platform.agent_runtime.background_run as background_run_module
from narranexus.platform.agent_runtime.background_run import BackgroundRun


class _FakeRuntime:
    """Async-context-manager stand-in for AgentRuntime that records the
    kwargs run() was called with and yields nothing."""

    captured: dict[str, Any] = {}

    async def __aenter__(self) -> "_FakeRuntime":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def run(self, **kwargs: Any):
        _FakeRuntime.captured = kwargs
        return
        yield  # pragma: no cover — makes this an async generator


async def _drive_and_capture(db_client, monkeypatch, **drive_extra: Any) -> dict[str, Any]:
    _FakeRuntime.captured = {}
    monkeypatch.setattr(agent_runtime_module, "AgentRuntime", _FakeRuntime)

    async def _noop(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(background_run_module, "_fire_message_success", _noop)
    monkeypatch.setattr(background_run_module, "_fire_message_failure", _noop)

    bg = BackgroundRun(
        agent_id="agent_test",
        user_id="u_test",
        input_preview="hi",
        db=db_client,
        active_runs={},
    )
    await bg.drive(
        agent_id="agent_test",
        user_id="u_test",
        input_content="hi",
        working_source="chat",
        **drive_extra,
    )
    hb = bg.recorder._heartbeat_task
    if hb:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    return _FakeRuntime.captured


@pytest.mark.asyncio
async def test_drive_forwards_fast_mode_true(db_client, monkeypatch):
    captured = await _drive_and_capture(db_client, monkeypatch, fast_mode=True)
    assert captured["fast_mode"] is True


@pytest.mark.asyncio
async def test_drive_defaults_fast_mode_false(db_client, monkeypatch):
    captured = await _drive_and_capture(db_client, monkeypatch)
    assert captured["fast_mode"] is False


@pytest.mark.asyncio
async def test_drive_defaults_steering_to_none(db_client, monkeypatch):
    # A run with no steer channel forwards steering=None → the loop mounts no
    # inlet (today's behaviour). Pins the default so a wiring change can't
    # silently start passing a stray object.
    captured = await _drive_and_capture(db_client, monkeypatch)
    assert captured["steering"] is None


@pytest.mark.asyncio
async def test_drive_forwards_the_steering_channel(db_client, monkeypatch):
    # The mid-run steering wire: a SteerChannel handed to BackgroundRun reaches
    # AgentRuntime.run(steering=…) verbatim (drive is pure passthrough, like
    # fast_mode). Without this the owner's follow-up never reaches the loop.
    _FakeRuntime.captured = {}
    monkeypatch.setattr(agent_runtime_module, "AgentRuntime", _FakeRuntime)

    async def _noop(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(background_run_module, "_fire_message_success", _noop)
    monkeypatch.setattr(background_run_module, "_fire_message_failure", _noop)

    sentinel = object()  # stands in for the SteerChannel
    bg = BackgroundRun(
        agent_id="agent_test", user_id="u_test", input_preview="hi",
        db=db_client, active_runs={}, steering=sentinel,
    )
    await bg.drive(
        agent_id="agent_test", user_id="u_test",
        input_content="hi", working_source="chat",
    )
    hb = bg.recorder._heartbeat_task
    if hb:
        hb.cancel()
        try:
            await hb
        except asyncio.CancelledError:
            pass
    assert _FakeRuntime.captured["steering"] is sentinel
