"""
@file_name: test_temporal_context.py
@author: Bin Liang
@date: 2026-04-21
@description: Tests for the User Temporal Context prompt block.

2026-07-25 (R4a turn-context relocation): the block's BUILD SITE moved —
with `prompt_turn_context_relocation_enabled` on (default) it renders into
the [Turn context] block of the current user message instead of the system
prompt; the heading string "User Temporal Context" is unchanged (job MCP
tool docstrings reference it). `build_user_temporal_block` itself is
unchanged and keeps its direct tests below.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from narranexus.platform.context_runtime.prompts import USER_TEMPORAL_CONTEXT
from narranexus.platform.settings import settings


def test_user_temporal_context_template_fields():
    block = USER_TEMPORAL_CONTEXT.format(user_tz="Asia/Shanghai")
    assert "Asia/Shanghai" in block
    assert "timezone" in block.lower()


def test_user_temporal_context_states_no_second_now():
    """The block must not render a "now" of its own (2026-08-18).

    It used to emit `Current local time: 2026-04-21T14:32:00` — naive, no
    offset, no weekday — the precise format a prior incident was traced to,
    sitting in the same turn-context block as the correctly-framed "Real
    World Information" value. Two "now"s cannot both be the ground truth,
    and the wrong-shaped one can only add doubt. Delegation stays; the
    duplicate value goes.
    """
    block = USER_TEMPORAL_CONTEXT.format(user_tz="Asia/Shanghai")
    assert "Current local time" not in block
    assert "Real World Information" in block
    # No bare naive datetime literal anywhere in the block.
    import re as _re
    assert not _re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", block)


def test_user_temporal_context_forbids_mental_date_arithmetic():
    """The prompt half of the fix: the tools exist, the agent must be told
    to prefer them over reasoning "下周五" out in its head."""
    block = USER_TEMPORAL_CONTEXT.format(user_tz="Asia/Shanghai")
    assert "resolve_relative_date" in block
    assert "compare_dates" in block


@pytest.mark.asyncio
async def testbuild_user_temporal_block_uses_user_timezone(db_client):
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime

    # Seed a user row with a specific timezone
    await db_client.insert("users", {
        "user_id": "u_tz_test",
        "display_name": "tz_user",
        "user_type": "user",
        "timezone": "Asia/Shanghai",
        "status": "active",
    })

    # Minimal ContextRuntime instance — we only exercise the helper
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_unused"

    block = await runtime.build_user_temporal_block("u_tz_test")
    assert "Asia/Shanghai" in block
    # No date literal: the block names the timezone and delegates "now" to
    # Real World Information (see test_user_temporal_context_states_no_second_now).
    from datetime import datetime
    assert str(datetime.now().year) not in block


@pytest.mark.asyncio
async def testbuild_user_temporal_block_absent_user_returns_empty(db_client):
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_unused"

    block = await runtime.build_user_temporal_block(None)
    assert block == ""


async def _seeded_runtime(db_client):
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime

    await db_client.insert("users", {
        "user_id": "u_tz_site",
        "display_name": "tz_site_user",
        "user_type": "user",
        "timezone": "Asia/Shanghai",
        "status": "active",
    })
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_tz_site"
    runtime.user_id = "u_tz_site"
    return runtime


def _ctx(user_id: str = "u_tz_site"):
    from narranexus.platform.schema import ContextData

    return ContextData(agent_id="agent_tz_site", user_id=user_id, input_content="hi")


@pytest.mark.asyncio
async def test_relocation_on_temporal_moves_to_turn_context(db_client, monkeypatch):
    """Flag ON: the temporal block leaves the system prompt and appears —
    same heading — in the [Turn context] block of the current message."""
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = await _seeded_runtime(db_client)
    ctx = _ctx()

    system_prompt = await runtime.build_complete_system_prompt(
        narrative_list=[], selected_events=[], module_instructions_list=[], ctx_data=ctx,
    )
    assert "## User Temporal Context" not in system_prompt

    final_messages, _mcp, _dis, _expr, _deferred = await runtime.build_input_for_framework(
        messages=[], system_prompt=system_prompt, active_instances=[], ctx_data=ctx,
    )
    user_msg = final_messages[-1]["content"]
    assert "## User Temporal Context" in user_msg
    assert "Asia/Shanghai" in user_msg


@pytest.mark.asyncio
async def test_relocation_off_temporal_stays_in_system_prompt(db_client, monkeypatch):
    """Flag OFF: original behavior — temporal in the system prompt, current
    message untouched."""
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", False)
    runtime = await _seeded_runtime(db_client)
    ctx = _ctx()

    system_prompt = await runtime.build_complete_system_prompt(
        narrative_list=[], selected_events=[], module_instructions_list=[], ctx_data=ctx,
    )
    assert "## User Temporal Context" in system_prompt
    assert "Asia/Shanghai" in system_prompt

    final_messages, _mcp, _dis, _expr, _deferred = await runtime.build_input_for_framework(
        messages=[], system_prompt=system_prompt, active_instances=[], ctx_data=ctx,
    )
    assert final_messages[-1]["content"] == "hi"
