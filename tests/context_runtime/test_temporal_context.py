"""
@file_name: test_temporal_context.py
@author: Bin Liang
@date: 2026-04-21
@description: Tests for the User Temporal Context prompt block.

2026-07-25 (R4a turn-context relocation): the block's BUILD SITE moved —
with `prompt_turn_context_relocation_enabled` on (default) it renders into
the [Turn context] block of the current user message instead of the system
prompt; the heading string "User Temporal Context" is unchanged (job MCP
tool docstrings reference it). `_build_user_temporal_block` itself is
unchanged and keeps its direct tests below.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.context_runtime.prompts import USER_TEMPORAL_CONTEXT
from xyz_agent_context.settings import settings


def test_user_temporal_context_template_fields():
    block = USER_TEMPORAL_CONTEXT.format(
        user_tz="Asia/Shanghai",
        now_local="2026-04-21T14:32:00",
    )
    assert "Asia/Shanghai" in block
    assert "2026-04-21T14:32:00" in block
    assert "timezone" in block.lower()


@pytest.mark.asyncio
async def test_build_user_temporal_block_uses_user_timezone(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

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

    block = await runtime._build_user_temporal_block("u_tz_test")
    assert "Asia/Shanghai" in block
    # Current date will appear — format is ISO 8601; just check the year
    from datetime import datetime
    year = str(datetime.now().year)
    assert year in block


@pytest.mark.asyncio
async def test_build_user_temporal_block_absent_user_returns_empty(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_unused"

    block = await runtime._build_user_temporal_block(None)
    assert block == ""


async def _seeded_runtime(db_client):
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

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
    from xyz_agent_context.schema import ContextData

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

    final_messages, _mcp, _dis, _expr = await runtime.build_input_for_framework(
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

    final_messages, _mcp, _dis, _expr = await runtime.build_input_for_framework(
        messages=[], system_prompt=system_prompt, active_instances=[], ctx_data=ctx,
    )
    assert final_messages[-1]["content"] == "hi"
