"""
@file_name: test_wechat_outbound_routing.py
@author:
@date: 2026-08-10
@description: Tests for wechat_module/wechat_outbound.py and the three
call sites that must route through it.

The router is the single decision point for "direct iLink vs platform
channel-send": when the provider is declared managed AND the manyfold env
resolves, sends go to the platform (no context_token needed); otherwise
the legacy direct path runs unchanged. The call-site tests pin that
wechat_send (MCP tool), send_to_agent (ChannelSenderRegistry), and
send_channel_reply (managed error fallback) all delegate to the router —
a new send site bypassing it would reintroduce the split-brain this
design exists to prevent.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from narranexus_plugins.wechat_module import wechat_outbound


def _cred(agent_id="agent_w1"):
    return SimpleNamespace(
        agent_id=agent_id,
        bot_token="bot-tok",
        base_url="https://ilink.example.com",
    )


class TestRouter:
    @pytest.mark.asyncio
    async def test_direct_path_when_not_managed(self, monkeypatch):
        direct = AsyncMock(return_value=True)
        managed = AsyncMock()
        monkeypatch.setattr(wechat_outbound, "send_text_once", direct)
        monkeypatch.setattr(wechat_outbound, "channel_send", managed)
        monkeypatch.setattr(
            wechat_outbound, "managed_channel_send_active", lambda p: False
        )

        ok = await wechat_outbound.send_wechat_text(
            agent_id="agent_w1",
            credential=_cred(),
            to_user_id="wx_peer",
            context_token="ctx-1",
            text="hi",
        )
        assert ok is True
        direct.assert_awaited_once_with(
            "bot-tok", "https://ilink.example.com", "wx_peer", "ctx-1", "hi"
        )
        managed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_managed_path_routes_channel_send(self, monkeypatch):
        direct = AsyncMock()
        managed = AsyncMock(return_value="delivered")
        monkeypatch.setattr(wechat_outbound, "send_text_once", direct)
        monkeypatch.setattr(wechat_outbound, "channel_send", managed)
        monkeypatch.setattr(
            wechat_outbound, "managed_channel_send_active", lambda p: p == "wechat"
        )

        ok = await wechat_outbound.send_wechat_text(
            agent_id="agent_w1",
            credential=_cred(),
            to_user_id="wx_peer",
            context_token="",  # channel-send needs no token
            text="hi",
        )
        assert ok is True
        direct.assert_not_awaited()
        managed.assert_awaited_once_with(
            agent_id="agent_w1",
            provider="wechat",
            room_id="wx_peer",
            text="hi",
        )

    @pytest.mark.asyncio
    async def test_managed_failure_surfaces_false_without_direct_fallback(
        self, monkeypatch
    ):
        """A failed platform send must NOT silently retry via direct iLink —
        managed mode means the platform owns delivery; a sandbox-side direct
        send would race the platform's own retry (double message)."""
        direct = AsyncMock()
        managed = AsyncMock(return_value="failed")
        monkeypatch.setattr(wechat_outbound, "send_text_once", direct)
        monkeypatch.setattr(wechat_outbound, "channel_send", managed)
        monkeypatch.setattr(
            wechat_outbound, "managed_channel_send_active", lambda p: True
        )

        ok = await wechat_outbound.send_wechat_text(
            agent_id="agent_w1",
            credential=_cred(),
            to_user_id="wx_peer",
            context_token="ctx",
            text="hi",
        )
        assert ok is False
        direct.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unavailable_platform_falls_back_to_direct(self, monkeypatch):
        """Endpoint missing (#511 not deployed / bad URL derivation) means
        the platform never received a delivery request — direct fallback
        cannot double-send, and without it every reply would be silently
        swallowed while the flag says managed."""
        direct = AsyncMock(return_value=True)
        managed = AsyncMock(return_value="unavailable")
        monkeypatch.setattr(wechat_outbound, "send_text_once", direct)
        monkeypatch.setattr(wechat_outbound, "channel_send", managed)
        monkeypatch.setattr(
            wechat_outbound, "managed_channel_send_active", lambda p: True
        )

        ok = await wechat_outbound.send_wechat_text(
            agent_id="agent_w1",
            credential=_cred(),
            to_user_id="wx_peer",
            context_token="ctx-4",
            text="hi",
        )
        assert ok is True
        managed.assert_awaited_once()
        direct.assert_awaited_once_with(
            "bot-tok", "https://ilink.example.com", "wx_peer", "ctx-4", "hi"
        )


class TestCallSitesDelegate:
    @pytest.mark.asyncio
    async def test_module_send_to_agent_uses_router(self, monkeypatch):
        from narranexus_plugins.wechat_module import wechat_module as wm

        router = AsyncMock(return_value=True)
        monkeypatch.setattr(wm, "send_wechat_text", router)
        module = wm.WeChatModule.__new__(wm.WeChatModule)
        monkeypatch.setattr(
            type(module), "get_credential", AsyncMock(return_value=_cred()), raising=False
        )

        result = await module.send_to_agent(
            "agent_w1", "wx_peer", "msg", context_token="ctx-9"
        )
        assert result == {"success": True}
        router.assert_awaited_once_with(
            agent_id="agent_w1",
            credential=router.await_args.kwargs["credential"],
            to_user_id="wx_peer",
            context_token="ctx-9",
            text="msg",
        )

    @pytest.mark.asyncio
    async def test_trigger_send_channel_reply_uses_router(self, monkeypatch):
        from narranexus_plugins.wechat_module import wechat_trigger as wt

        router = AsyncMock(return_value=True)
        monkeypatch.setattr(wt, "send_wechat_text", router)
        trigger = wt.WeChatTrigger.__new__(wt.WeChatTrigger)
        message = SimpleNamespace(
            sender_id="wx_peer", raw={"context_token": "ctx-2"}
        )

        await trigger.send_channel_reply(_cred(), message, "sorry, run failed")
        router.assert_awaited_once()
        kwargs = router.await_args.kwargs
        assert kwargs["agent_id"] == "agent_w1"
        assert kwargs["to_user_id"] == "wx_peer"
        assert kwargs["context_token"] == "ctx-2"

    @pytest.mark.asyncio
    async def test_mcp_wechat_send_uses_router(self, monkeypatch):
        from narranexus_plugins.wechat_module import _wechat_mcp_tools as tools

        router = AsyncMock(return_value=True)
        monkeypatch.setattr(tools, "send_wechat_text", router)

        # wechat_send now reads its credential through the seam helper
        # (_get_credential -> ChannelCredentialStore), not a local manager.
        monkeypatch.setattr(tools, "_get_credential", AsyncMock(return_value=_cred()))

        registered: dict[str, object] = {}

        class _FakeMCP:
            def tool(self):
                def deco(fn):
                    registered[fn.__name__] = fn
                    return fn

                return deco

        tools.register_wechat_mcp_tools(_FakeMCP())
        result = await registered["wechat_send"](
            agent_id="agent_w1",
            to_user_id="wx_peer",
            context_token="ctx-3",
            text="hello",
        )
        assert result == {"ok": True}
        router.assert_awaited_once()
        assert router.await_args.kwargs["to_user_id"] == "wx_peer"
