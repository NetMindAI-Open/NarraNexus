"""
@file_name: test_setup_residency.py
@date: 2026-07-24
@description: Setup-residency (B++) contract tests across ALL IM channel
modules.

While an agent has no binding for a channel, the module stays loaded but
degrades to a "setup surface": instructions collapse to a one-liner and
every tool except the ones in ``setup_tool_names`` is suppressed via
``get_disallowed_tools``. This file pins the cross-channel contract:

  1. Drift guard — ``all_tool_names`` equals the set of tools the module
     actually registers on its FastMCP server, so a newly added tool
     can't silently keep shipping its schema to unbound agents.
  2. Unbound → every non-setup tool is disallowed (fully-qualified
     ``mcp__<server>__<tool>``) and the one-line instruction names the
     setup tool(s) (or points at Settings for WeChat, which has no
     in-chat bind tool).
  3. Bound → nothing is disallowed.
  4. Credential lookup failure → FAIL-OPEN (treated as bound; nothing
     disallowed). Wrongly gating a bound channel is user-visible loss.
  5. Zero-arg bind tools return the full setup guide on demand (the
     walkthrough that left the per-turn system prompt).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from xyz_agent_context.channel.channel_module_base import ChannelModuleBase
from xyz_agent_context.channel.channel_sender_registry import ChannelSenderRegistry
from xyz_agent_context.module.discord_module.discord_module import DiscordModule
from xyz_agent_context.module.lark_module.lark_module import LarkModule
from xyz_agent_context.module.narramessenger_module.narramessenger_module import (
    NarramessengerModule,
)
from xyz_agent_context.module.slack_module.slack_module import SlackModule
from xyz_agent_context.module.telegram_module.telegram_module import TelegramModule
from xyz_agent_context.module.wechat_module.wechat_module import WeChatModule


CHANNEL_MODULES = [
    SlackModule,
    TelegramModule,
    DiscordModule,
    LarkModule,
    WeChatModule,
    NarramessengerModule,
]


@pytest.fixture(autouse=True)
def reset_sender_registry():
    """Isolate the class-level sender registration guards per test."""
    ChannelSenderRegistry._senders.clear()
    ChannelModuleBase._sender_registered_for_channel.clear()
    yield
    ChannelSenderRegistry._senders.clear()
    ChannelModuleBase._sender_registered_for_channel.clear()


def _make_module(cls) -> ChannelModuleBase:
    """Construct a channel module without touching a real database."""
    return cls(agent_id="agent_a", user_id=None, database_client=MagicMock())


# ── 1. Drift guard: all_tool_names ⇔ actually registered tools ─────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", CHANNEL_MODULES, ids=lambda c: c.__name__)
async def test_all_tool_names_matches_registered_mcp_tools(cls):
    module = _make_module(cls)
    mcp = module.create_mcp_server()
    assert mcp is not None, f"{cls.__name__}.create_mcp_server returned None"

    registered = {t.name for t in await mcp.list_tools()}
    assert registered == set(module.all_tool_names), (
        f"{cls.__name__}.all_tool_names drifted from the tools its "
        f"register_mcp_tools actually registers"
    )
    # No duplicates in the declared tuple, and setup tools are a subset.
    assert len(module.all_tool_names) == len(set(module.all_tool_names))
    assert module.setup_tool_names <= set(module.all_tool_names)


# ── 2. Unbound → non-setup tools suppressed + one-line instruction ─────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", CHANNEL_MODULES, ids=lambda c: c.__name__)
async def test_unbound_disallows_every_non_setup_tool(cls, monkeypatch):
    module = _make_module(cls)
    monkeypatch.setattr(module, "get_credential", AsyncMock(return_value=None))

    disallowed = await module.get_disallowed_tools()
    expected = sorted(
        f"mcp__{module.mcp_server_name}__{name}"
        for name in module.all_tool_names
        if name not in module.setup_tool_names
    )
    assert sorted(disallowed) == expected

    line = module.unbound_setup_line()
    if module.setup_tool_names:
        for name in module.setup_tool_names:
            assert name in line
    else:
        # WeChat: QR binding is frontend-only → point at Settings.
        assert "Settings" in line


# ── 3. Bound → nothing suppressed ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", CHANNEL_MODULES, ids=lambda c: c.__name__)
async def test_bound_disallows_nothing(cls, monkeypatch):
    module = _make_module(cls)
    monkeypatch.setattr(module, "get_credential", AsyncMock(return_value=object()))

    assert await module.get_disallowed_tools() == []


# ── 4. Credential lookup failure → fail-open ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", CHANNEL_MODULES, ids=lambda c: c.__name__)
async def test_credential_error_fails_open(cls, monkeypatch):
    module = _make_module(cls)

    async def _boom(agent_id: str):
        raise RuntimeError("db down")

    monkeypatch.setattr(module, "get_credential", _boom)

    assert await module.is_bound() is True
    assert await module.get_disallowed_tools() == []


# ── 5. Zero-arg bind tools serve the full setup guide ──────────────────


class _CaptureMCP:
    """Captures the functions registered via ``@mcp.tool()`` (same idiom
    as tests/channel/test_react_tool.py) so the tool closures can be
    invoked directly without a FastMCP transport."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *_a, **_k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _bind_tool(register_fn, tool_name: str):
    cap = _CaptureMCP()
    register_fn(cap)
    return cap.tools[tool_name]


def _bind_registrations():
    from xyz_agent_context.module.discord_module._discord_mcp_tools import (
        register_discord_mcp_tools,
    )
    from xyz_agent_context.module.narramessenger_module._narramessenger_mcp_tools import (
        register_narramessenger_mcp_tools,
    )
    from xyz_agent_context.module.slack_module._slack_mcp_tools import (
        register_slack_mcp_tools,
    )
    from xyz_agent_context.module.telegram_module._telegram_mcp_tools import (
        register_telegram_mcp_tools,
    )

    return [
        pytest.param(register_slack_mcp_tools, "slack_bind", id="slack"),
        pytest.param(register_telegram_mcp_tools, "tg_bind", id="telegram"),
        pytest.param(register_discord_mcp_tools, "discord_bind", id="discord"),
        pytest.param(
            register_narramessenger_mcp_tools, "narra_bind", id="narramessenger"
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("register_fn,tool_name", _bind_registrations())
async def test_bind_tool_zero_arg_returns_setup_guide(register_fn, tool_name):
    bind = _bind_tool(register_fn, tool_name)
    out = await bind(agent_id="agent_a")

    assert out["success"] is True
    assert len(out["setup_guide"]) > 500


@pytest.mark.asyncio
async def test_lark_entry_tools_zero_credential_args_return_setup_guide():
    """Lark's entry points keep their orchestrator shape; calling them
    with empty credential args must yield the discovery guide, not an
    error."""
    from xyz_agent_context.module.lark_module._lark_mcp_tools import (
        register_lark_mcp_tools,
    )

    cap = _CaptureMCP()
    register_lark_mcp_tools(cap)

    setup_out = await cap.tools["lark_setup"](agent_id="agent_a")
    assert setup_out["success"] is True
    assert len(setup_out["setup_guide"]) > 500

    bind_out = await cap.tools["lark_bind"](agent_id="agent_a")
    assert bind_out["success"] is True
    assert len(bind_out["setup_guide"]) > 500
