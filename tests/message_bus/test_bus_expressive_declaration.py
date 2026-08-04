"""
@file_name: test_bus_expressive_declaration.py
@date: 2026-08-04
@description: MessageBusModule declares the bus delivery tools as the turn's
reply surface — but ONLY on bus-triggered turns (NexusPower reply contract,
origin-aware declaration).

Why gating matters both ways:
- A bus-triggered turn without this declaration tells the model (via the
  framework's per-step reply reminder) that ONLY the owner-chat tool
  delivers — so finished work ends as undelivered plain text, or gets
  misdelivered to the owner's web chat (2026-08-01 briefing-squad incident).
- A chat turn WITH this declaration would invite replying to the owner via
  the bus. A team-room turn WITH it would invite double-posting (the room
  auto-posts plain text; its prompt forbids delivery tools).

Drift guards mirror tests/chat_module/test_expressive_declaration.py: the
declaration derives from get_mcp_config().server_name, and the short names
must be tools the bus MCP server actually registers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from xyz_agent_context.module.message_bus_module.message_bus_module import (
    MessageBusModule,
)
from xyz_agent_context.schema import ContextData
from xyz_agent_context.schema.hook_schema import WorkingSource


def _module() -> MessageBusModule:
    return MessageBusModule(
        agent_id="agent_a", user_id=None, database_client=MagicMock()
    )


def _ctx(working_source, **extra) -> ContextData:
    ctx = ContextData(agent_id="agent_a", user_id=None, input_content="hi")
    ctx.working_source = working_source
    ctx.extra_data.update(extra)
    return ctx


@pytest.mark.asyncio
async def test_bus_turn_declares_bus_delivery_tools():
    module = _module()
    mcp_config = await module.get_mcp_config()
    declared = await module.get_expressive_tools(
        _ctx(WorkingSource.MESSAGE_BUS)
    )
    assert declared == [
        f"mcp__{mcp_config.server_name}__bus_send_message",
        f"mcp__{mcp_config.server_name}__bus_send_to_agent",
    ]


@pytest.mark.asyncio
async def test_bus_turn_with_serialized_source_string_also_declares():
    declared = await _module().get_expressive_tools(
        _ctx(WorkingSource.MESSAGE_BUS.value)
    )
    assert any(t.endswith("__bus_send_message") for t in declared)


@pytest.mark.asyncio
async def test_chat_turn_declares_nothing():
    assert await _module().get_expressive_tools(_ctx(WorkingSource.CHAT)) == []


@pytest.mark.asyncio
async def test_no_ctx_declares_nothing():
    """Legacy/no-ctx callers keep the empty default — never advertise bus
    tools as the reply surface of a turn whose origin is unknown."""
    assert await _module().get_expressive_tools() == []


@pytest.mark.asyncio
async def test_team_room_turn_declares_nothing():
    """Team rooms deliver via plain-text auto-post; their prompt forbids
    delivery tools. Declaring bus tools there would invite double-posting."""
    declared = await _module().get_expressive_tools(
        _ctx(WorkingSource.MESSAGE_BUS, bus_team_room="1")
    )
    assert declared == []


@pytest.mark.asyncio
async def test_declared_short_names_are_actually_registered():
    module = _module()
    mcp = module.create_mcp_server()
    assert mcp is not None
    registered = {t.name for t in await mcp.list_tools()}
    assert {"bus_send_message", "bus_send_to_agent"} <= registered
