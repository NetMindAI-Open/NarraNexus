"""
@file_name: test_discord_mcp_and_text.py
@date: 2026-06-16
@description: Unit tests for Discord MCP tools + text sanitizer + reply extractor.

MCP tools are registered into a fake FastMCP that records the tool
functions, then invoked directly with monkeypatched credential/manager/
SDK so no network or real FastMCP server is involved.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.discord_module import _discord_mcp_tools as mcp_mod
from xyz_agent_context.module.discord_module._discord_credential_manager import (
    DiscordCredential,
)
from xyz_agent_context.module.discord_module._discord_text_sanitizer import (
    DISCORD_MESSAGE_LIMIT,
    split_discord_message,
)
from xyz_agent_context.module.discord_module.discord_module import _extract_discord_reply


# ── text sanitizer ─────────────────────────────────────────────────────


def test_split_short_message_single_chunk():
    assert split_discord_message("hi") == ["hi"]


def test_split_empty_returns_single_empty():
    assert split_discord_message("") == [""]


def test_split_respects_limit():
    chunks = split_discord_message("x" * (DISCORD_MESSAGE_LIMIT * 2 + 37))
    assert all(len(c) <= DISCORD_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks) == "x" * (DISCORD_MESSAGE_LIMIT * 2 + 37)


def test_split_prefers_newline_boundary():
    body = ("a" * 1990) + "\n" + ("b" * 100)
    chunks = split_discord_message(body)
    assert chunks[0] == "a" * 1990  # split on the newline, not mid-run
    assert chunks[1] == "b" * 100


# ── reply extractor (MessageSourceRegistry) ────────────────────────────


def test_extract_reply_from_discord_send():
    assert _extract_discord_reply("discord_send", {"text": "yo"}) == "yo"


def test_extract_reply_from_discord_reply():
    assert _extract_discord_reply("discord_reply", {"text": "re"}) == "re"


def test_extract_reply_from_generic_chat_tool():
    assert _extract_discord_reply("send_message_to_user_directly", {"content": "c"}) == "c"


def test_extract_reply_none_for_history_tool():
    assert _extract_discord_reply("discord_read_history", {"channel_id": "c"}) is None


# ── MCP tools ──────────────────────────────────────────────────────────


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


class _FakeSDK:
    def __init__(self, token, *, send_result=None, dm_channel="dm1", guilds=None, channels=None):
        self._send_result = send_result or {"id": "999"}
        self._dm_channel = dm_channel
        self._guilds = guilds if guilds is not None else [{"id": "g1", "name": "Srv"}]
        self._channels = channels if channels is not None else [
            {"id": "c1", "name": "general", "type": 0},
            {"id": "c2", "name": "voice", "type": 2},  # voice — must be filtered out
            {"id": "c3", "name": "news", "type": 5},
        ]

    async def send_message(self, channel_id, text):
        return self._send_result

    async def create_reply(self, channel_id, message_id, text):
        return self._send_result

    async def create_dm_channel(self, user_id):
        return self._dm_channel

    async def list_guilds(self):
        return self._guilds

    async def list_guild_channels(self, guild_id):
        return self._channels


def _register() -> _FakeMCP:
    mcp = _FakeMCP()
    mcp_mod.register_discord_mcp_tools(mcp)
    return mcp


@pytest.mark.asyncio
async def test_discord_send_happy(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))

    mcp = _register()
    res = await mcp.tools["discord_send"]("a", "chan1", "hello")
    assert res["success"] is True
    assert res["data"]["message_id"] == "999"


@pytest.mark.asyncio
async def test_discord_send_no_credential(monkeypatch):
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(None))
    mcp = _register()
    res = await mcp.tools["discord_send"]("a", "chan1", "hello")
    assert res["success"] is False
    assert res["error"] == "no_credential"


@pytest.mark.asyncio
async def test_discord_send_requires_args(monkeypatch):
    mcp = _register()
    res = await mcp.tools["discord_send"]("a", "", "")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_discord_dm_opens_channel_and_sends(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))
    mcp = _register()
    res = await mcp.tools["discord_dm"]("a", "123456", "hi there")
    assert res["success"] is True
    assert res["data"]["channel_id"] == "dm1"
    assert res["data"]["message_id"] == "999"


@pytest.mark.asyncio
async def test_discord_dm_requires_args(monkeypatch):
    mcp = _register()
    res = await mcp.tools["discord_dm"]("a", "", "")
    assert res["success"] is False


# ── whitespace-only reply guard ────────────────────────────────────────
# A degenerate whitespace-only reply ("   ", "\n") used to slip past the
# `if not text` guard and post a blank-looking Discord message. The guard
# now rejects whitespace-only text so a degenerate turn fails loudly (and
# the inbox records "(stayed silent)") instead of posting an empty message.


@pytest.mark.asyncio
async def test_discord_send_rejects_whitespace_only(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))
    mcp = _register()
    res = await mcp.tools["discord_send"]("a", "chan1", "   \n  ")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_discord_reply_rejects_whitespace_only(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))
    mcp = _register()
    res = await mcp.tools["discord_reply"]("a", "chan1", "m1", "\n\n")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_discord_dm_rejects_whitespace_only(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))
    mcp = _register()
    res = await mcp.tools["discord_dm"]("a", "123", " ")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_discord_list_channels_filters_to_postable(monkeypatch):
    cred = DiscordCredential(agent_id="a", bot_token="MTA.tok", bot_user_id="B1")
    monkeypatch.setattr(mcp_mod, "_get_credential", lambda agent_id: _async(cred))
    monkeypatch.setattr(mcp_mod, "DiscordSDKClient", lambda token: _FakeSDK(token))
    mcp = _register()
    res = await mcp.tools["discord_list_channels"]("a")
    assert res["success"] is True
    ids = {c["channel_id"] for c in res["data"]}
    assert ids == {"c1", "c3"}  # text + announcement only; voice (c2) excluded
    assert res["data"][0]["guild_name"] == "Srv"


@pytest.mark.asyncio
async def test_discord_unbind_delegates(monkeypatch):
    class _FakeMgr:
        async def unbind(self, agent_id):
            return True

    monkeypatch.setattr(mcp_mod, "_get_manager", lambda: _async(_FakeMgr()))
    mcp = _register()
    res = await mcp.tools["discord_unbind"]("a")
    assert res["success"] is True
    assert res["data"]["unbound"] is True


async def _async(value):
    """Wrap a value as an awaitable (the monkeypatched helpers are async)."""
    return value
