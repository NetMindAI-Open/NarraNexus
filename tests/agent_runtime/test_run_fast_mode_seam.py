"""
@file_name: test_run_fast_mode_seam.py
@date: 2026-08-14
@description: AgentRuntime.run() actually applies fast_mode to the RunContext.

The pure resolver has its own tests; this file crosses the seam they
don't: revert the ``turn_profile = _resolve_turn_profile(...)`` line in
run() and these go red.

The capture itself lives in `conftest.py` (`capture_run_context`). These
tests deliberately do NOT pass a `db_client` — part of what they cover is
that the profile reaches RunContext on a run with no database of its own.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_fast_mode_true_reaches_run_context(capture_run_context):
    captured, _ = await capture_run_context(fast_mode=True)
    assert captured["turn_profile"] is not None
    assert captured["turn_profile"].name == "chat_fast"


@pytest.mark.asyncio
async def test_default_run_context_has_no_profile(capture_run_context):
    captured, _ = await capture_run_context()
    assert captured["turn_profile"] is None
