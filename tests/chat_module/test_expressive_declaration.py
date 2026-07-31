"""
@file_name: test_expressive_declaration.py
@date: 2026-07-31
@description: ChatModule declares the owner-chat delivery tool (NexusPower
reply contract). ChatModule is priority 1, so the (priority, module_class)
sorted collection makes this the turn's default reply tool — the name the
framework's constitution renders as its example.

Drift guards mirror the channel side (test_setup_residency.py §6): the
declaration is derived from get_mcp_config().server_name (never a literal
that a server rename would silently orphan), and the short name must be a
tool the chat MCP server actually registers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule


def _module() -> ChatModule:
    return ChatModule(agent_id="agent_a", user_id=None, database_client=MagicMock())


@pytest.mark.asyncio
async def test_declaration_derives_from_mcp_server_name():
    module = _module()
    mcp_config = await module.get_mcp_config()
    assert await module.get_expressive_tools() == [
        f"mcp__{mcp_config.server_name}__send_message_to_user_directly"
    ]


@pytest.mark.asyncio
async def test_declared_short_name_is_actually_registered():
    module = _module()
    mcp = module.create_mcp_server()
    assert mcp is not None
    registered = {t.name for t in await mcp.list_tools()}
    assert "send_message_to_user_directly" in registered