"""
@file_name: test_step_1_5_read_flag.py
@date: 2026-08-06
@description: step_1_5 read_history flag — fast turns keep the cheap outputs.

Review finding #4: fast turns must NOT skip step_1_5 wholesale. The LLM
round trip it feeds is already bypassed, but two byproducts are load-
bearing: ctx.previous_instances (trajectory snapshot persisted by
step_4) and initialize_markdown (a narrative first hit by a voice turn
would otherwise never get its md file, silently freezing statistics).

Locks:
- read_history=False: previous_instances snapshotted, initialize called,
  read_markdown NOT called (the only part whose output feeds the skipped
  decision LLM).
- read_history default True: all three run (normal path unchanged).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus.platform.agent_runtime._agent_runtime_steps.step_1_5_init_markdown import (
    step_1_5_init_markdown,
)
from narranexus.platform.agent_runtime._agent_runtime_steps.context import RunContext


def _ctx_with_narrative():
    ctx = RunContext(
        agent_id="a", user_id="u", input_content="q", working_source="chat"
    )
    narrative = SimpleNamespace(id="nar_1", active_instances=[{"id": "i1"}])
    ctx.narrative_list = [narrative]
    return ctx, narrative


def _manager():
    return SimpleNamespace(
        initialize_markdown=AsyncMock(),
        read_markdown=AsyncMock(return_value="HISTORY"),
    )


@pytest.mark.asyncio
async def test_fast_mode_keeps_cheap_outputs_skips_read():
    ctx, narrative = _ctx_with_narrative()
    mm = _manager()
    await step_1_5_init_markdown(ctx, mm, read_history=False)
    assert ctx.previous_instances == [{"id": "i1"}]
    assert ctx.previous_instances is not narrative.active_instances  # deep copy
    mm.initialize_markdown.assert_awaited_once_with(narrative)
    mm.read_markdown.assert_not_awaited()
    assert ctx.markdown_history == ""


@pytest.mark.asyncio
async def test_default_reads_history_as_before():
    ctx, _ = _ctx_with_narrative()
    mm = _manager()
    await step_1_5_init_markdown(ctx, mm)
    mm.read_markdown.assert_awaited_once_with("nar_1")
    assert ctx.markdown_history == "HISTORY"
