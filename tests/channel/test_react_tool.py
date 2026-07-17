"""
@file_name: test_react_tool.py
@author: NarraNexus
@date: 2026-07-10
@description: The unified agent-facing ``react_to_user_message`` tool — the
agent-driven IM early-feedback capability. Each IM module exposes the same-named
tool with a shared semantic emoji vocabulary (on_it/done/thumbs_up/heart/problem)
mapped to its platform's own tokens, backed by the per-channel SDK
``add_reaction``. WeChat has no reaction API and returns the unsupported
envelope. Every path is best-effort — a failing SDK call returns
``{"success": false}`` and never raises.

Also covers the enabler: the inbound ``source_message_id`` is surfaced into
``trigger_extra_data`` so the per-channel instructions can tell the agent which
message to react to.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from xyz_agent_context.schema.parsed_message import ParsedMessage
from tests.channel.test_mock_channel_trigger_integration import (
    _FakeCredential,
    _FakeTrigger,
)


class _FakeMCP:
    """Captures the functions registered via ``@mcp.tool()``."""

    def __init__(self):
        self.tools: dict = {}

    def tool(self, *_a, **_k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tools(register_fn):
    m = _FakeMCP()
    register_fn(m)
    return m.tools


@dataclass
class _Cred:
    bot_token: str = "tok"
    base_url: str = "https://x"
    agent_id: str = "agent_a"


# ── Lark ─────────────────────────────────────────────────────────────


class _FakeLarkCli:
    def __init__(self, *, raises=False):
        self.calls: list[tuple[str, str]] = []
        self.raises = raises

    async def add_reaction(self, agent_id, message_id, emoji_type):
        self.calls.append((message_id, emoji_type))
        if self.raises:
            raise RuntimeError("no scope")
        return f"rid_{emoji_type}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "semantic,expected",
    [
        ("on_it", "Typing"),
        ("done", "DONE"),
        ("thumbs_up", "THUMBSUP"),
        ("searching", "GLANCE"),
        ("celebrate", "FIRECRACKER"),
        ("problem", "CrossMark"),
        ("nope", "Typing"),  # unknown → default on_it
    ],
)
async def test_lark_react_maps_semantic_to_emoji_type(monkeypatch, semantic, expected):
    import xyz_agent_context.module.lark_module._lark_mcp_tools as m

    fake = _FakeLarkCli()
    monkeypatch.setattr(m, "_cli", fake)
    react = _tools(m.register_lark_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "oc_room", "om_1", semantic)
    assert out["success"] is True
    assert fake.calls == [("om_1", expected)]


@pytest.mark.asyncio
async def test_lark_react_missing_message_id(monkeypatch):
    import xyz_agent_context.module.lark_module._lark_mcp_tools as m

    monkeypatch.setattr(m, "_cli", _FakeLarkCli())
    react = _tools(m.register_lark_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "oc_room", "", "on_it")
    assert out["success"] is False


@pytest.mark.asyncio
async def test_lark_react_swallows_sdk_error(monkeypatch):
    import xyz_agent_context.module.lark_module._lark_mcp_tools as m

    monkeypatch.setattr(m, "_cli", _FakeLarkCli(raises=True))
    react = _tools(m.register_lark_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "oc_room", "om_1", "on_it")  # must not raise
    assert out["success"] is False and "reason" in out


# ── Slack ────────────────────────────────────────────────────────────


class _FakeSlackClient:
    last = None

    def __init__(self, token):
        _FakeSlackClient.last = self
        self.calls: list[tuple[str, str, str]] = []

    async def add_reaction(self, channel, ts, name):
        self.calls.append((channel, ts, name))


@pytest.mark.asyncio
async def test_slack_react_maps_and_calls(monkeypatch):
    import xyz_agent_context.module.slack_module._slack_mcp_tools as m

    async def _cred(_a):
        return _Cred()

    monkeypatch.setattr(m, "_get_credential", _cred)
    monkeypatch.setattr(m, "SlackSDKClient", _FakeSlackClient)
    react = _tools(m.register_slack_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "C1", "1620.0001", "done")
    assert out["success"] is True
    assert _FakeSlackClient.last.calls == [("C1", "1620.0001", "white_check_mark")]


@pytest.mark.asyncio
async def test_slack_react_no_credential(monkeypatch):
    import xyz_agent_context.module.slack_module._slack_mcp_tools as m

    async def _cred(_a):
        return None

    monkeypatch.setattr(m, "_get_credential", _cred)
    react = _tools(m.register_slack_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "C1", "1620.0001", "on_it")
    assert out["success"] is False


# ── Discord ──────────────────────────────────────────────────────────


class _FakeDiscordClient:
    last = None

    def __init__(self, token):
        _FakeDiscordClient.last = self
        self.calls: list[tuple[str, str, str]] = []

    async def add_reaction(self, channel_id, message_id, emoji):
        self.calls.append((channel_id, message_id, emoji))


@pytest.mark.asyncio
async def test_discord_react_maps_and_calls(monkeypatch):
    import xyz_agent_context.module.discord_module._discord_mcp_tools as m

    async def _cred(_a):
        return _Cred()

    monkeypatch.setattr(m, "_get_credential", _cred)
    monkeypatch.setattr(m, "DiscordSDKClient", _FakeDiscordClient)
    react = _tools(m.register_discord_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "123", "456", "celebrate")
    assert out["success"] is True
    assert _FakeDiscordClient.last.calls == [("123", "456", "🎉")]


# ── Telegram ─────────────────────────────────────────────────────────


class _FakeTelegramClient:
    last = None

    def __init__(self, token):
        _FakeTelegramClient.last = self
        self.calls: list[tuple[str, str, str]] = []
        self.closed = False

    async def set_message_reaction(self, chat_id, message_id, emoji):
        self.calls.append((chat_id, message_id, emoji))
        return True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_telegram_react_maps_and_closes(monkeypatch):
    import xyz_agent_context.module.telegram_module._telegram_mcp_tools as m

    async def _cred(_a):
        return _Cred()

    monkeypatch.setattr(m, "_get_credential", _cred)
    monkeypatch.setattr(m, "TelegramSDKClient", _FakeTelegramClient)
    react = _tools(m.register_telegram_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "999", "7", "on_it")
    assert out["success"] is True
    assert _FakeTelegramClient.last.calls == [("999", "7", "👀")]
    assert _FakeTelegramClient.last.closed is True


# ── WeChat (unsupported) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wechat_react_unsupported():
    import xyz_agent_context.module.wechat_module._wechat_mcp_tools as m

    react = _tools(m.register_wechat_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "u1", "m1", "on_it")
    assert out["success"] is False
    assert "not supported" in out["reason"].lower()


# ── Telegram SDK: set_message_reaction ───────────────────────────────


@pytest.mark.asyncio
async def test_telegram_sdk_set_message_reaction(monkeypatch):
    from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
        TelegramSDKClient,
    )

    client = TelegramSDKClient("tok")
    seen = {}

    async def _api_call(method, args):
        seen["method"] = method
        seen["args"] = args
        return {"ok": True}

    monkeypatch.setattr(client, "api_call", _api_call)
    ok = await client.set_message_reaction("999", "7", "👍")
    assert ok is True
    assert seen["method"] == "setMessageReaction"
    assert seen["args"]["chat_id"] == "999"
    assert seen["args"]["message_id"] == 7  # coerced to int
    assert seen["args"]["reaction"] == [{"type": "emoji", "emoji": "👍"}]


@pytest.mark.asyncio
async def test_telegram_sdk_reaction_bad_message_id(monkeypatch):
    from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
        TelegramSDKClient,
    )

    client = TelegramSDKClient("tok")

    async def _api_call(method, args):  # should not be reached
        raise AssertionError("api_call must not be called for a bad message_id")

    monkeypatch.setattr(client, "api_call", _api_call)
    assert await client.set_message_reaction("999", "not-an-int", "👍") is False


# ── Enabler: source_message_id lands in trigger_extra_data ───────────


@pytest.mark.asyncio
async def test_source_message_id_in_trigger_extra_data(db_client, monkeypatch):
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    captured: dict = {}

    async def _fake_collect_run(*_a, **k):
        captured.update(k)

        @dataclass
        class _R:
            output_text: str = "ok"
            is_error: bool = False
            error: object = None
            raw_items: list = None

        return _R(raw_items=[])

    class _FakeAgentRuntime:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)

    trigger = _FakeTrigger([], _FakeCredential(agent_id="agent_a"))
    trigger._db = db_client
    msg = ParsedMessage(
        message_id="om_xyz", chat_id="C1", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )
    await trigger._build_and_run_agent(trigger._credential, msg, "Alice", attachments=[])

    extra = captured.get("trigger_extra_data") or {}
    assert extra.get("source_message_id") == "om_xyz"


@pytest.mark.asyncio
async def test_trigger_prepends_early_feedback_to_input(db_client, monkeypatch):
    """The trigger injects the 'ack early' directive into the per-turn input
    (input_content), right after the channel tag — using the channel's
    react_tool_ref + the real room/message ids."""
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    captured: dict = {}

    async def _fake_collect_run(*a, **k):
        captured["input_content"] = k.get("input_content")

        @dataclass
        class _R:
            output_text: str = "ok"
            is_error: bool = False
            error: object = None
            raw_items: list = None

        return _R(raw_items=[])

    class _FakeAgentRuntime:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)

    class _ReactTrigger(_FakeTrigger):
        react_tool_ref = "react_to_user_message"

    trigger = _ReactTrigger([], _FakeCredential(agent_id="agent_a"))
    trigger._db = db_client
    msg = ParsedMessage(
        message_id="om_1", chat_id="C9", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )
    await trigger._build_and_run_agent(trigger._credential, msg, "Alice", attachments=[])

    ic = captured.get("input_content")
    assert ic is not None, "input_content should reach collect_run as a kwarg"
    # channel tag first, then the ack directive, then the built prompt
    assert ic.startswith("[Fake")
    assert "\n**Early feedback**:" in ic
    assert 'react_to_user_message(agent_id, room_id="C9", message_id="om_1"' in ic


@pytest.mark.asyncio
async def test_trigger_message_only_ack_when_no_message_id(db_client, monkeypatch):
    """A react-capable channel with NO inbound message_id degrades to the
    message-only ack — it must NOT emit an empty prefix (regression: PR #90
    review Important #3)."""
    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod

    captured: dict = {}

    async def _fake_collect_run(*a, **k):
        captured["input_content"] = k.get("input_content")

        @dataclass
        class _R:
            output_text: str = "ok"
            is_error: bool = False
            error: object = None
            raw_items: list = None

        return _R(raw_items=[])

    class _FakeAgentRuntime:
        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _fake_collect_run)

    class _ReactTrigger(_FakeTrigger):
        react_tool_ref = "react_to_user_message"

    trigger = _ReactTrigger([], _FakeCredential(agent_id="agent_a"))
    trigger._db = db_client
    msg = ParsedMessage(
        message_id="", chat_id="C9", sender_id="u1",
        sender_name="Alice", content="hi", timestamp_ms=1,
    )
    await trigger._build_and_run_agent(trigger._credential, msg, "Alice", attachments=[])

    ic = captured.get("input_content") or ""
    assert "**Early feedback**:" in ic          # still acks
    assert "on it, one moment" in ic            # message-only form
    assert "react_to_user_message" not in ic    # no react tool (no message_id)


def test_lark_trigger_react_tool_ref_and_prefix():
    """Lark fully overrides _build_and_run_agent (manual copy of the injection),
    so pin its react_tool_ref to the fully-qualified name its prompts use, and
    that the inherited _early_feedback_prefix renders it (PR #90 review #4)."""
    from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger

    t = LarkTrigger()
    assert t.react_tool_ref == "mcp__lark_module__react_to_user_message"
    msg = ParsedMessage(
        message_id="om_9", chat_id="oc_1", sender_id="u",
        sender_name="A", content="x", timestamp_ms=1,
    )
    prefix = t._early_feedback_prefix(msg)
    assert (
        'mcp__lark_module__react_to_user_message(agent_id, room_id="oc_1", '
        'message_id="om_9"' in prefix
    )


def test_early_feedback_prefix_message_only_when_no_react_tool():
    """A trigger with react_tool_ref=None (WeChat) emits the message-only ack,
    with no react tool name."""
    cred = _FakeCredential(agent_id="agent_a")
    trigger = _FakeTrigger([], cred)  # base default react_tool_ref = None
    msg = ParsedMessage(
        message_id="m1", chat_id="C1", sender_id="u1",
        sender_name="A", content="hi", timestamp_ms=1,
    )
    prefix = trigger._early_feedback_prefix(msg)
    assert "on it, one moment" in prefix
    assert "react_to_user_message" not in prefix


# ── shared render_early_feedback ─────────────────────────────────────


def test_render_early_feedback_reaction_variant():
    from xyz_agent_context.channel.channel_reactions import (
        REACTION_VOCABULARY,
        render_early_feedback,
    )

    out = render_early_feedback(
        tool_ref="react_to_user_message", room_id="C1", message_id="m1",
    )
    assert 'react_to_user_message(agent_id, room_id="C1", message_id="m1"' in out
    assert "### Early feedback" in out
    for name in REACTION_VOCABULARY:  # the full menu is listed, one source
        assert name in out


def test_render_early_feedback_message_only_variant():
    from xyz_agent_context.channel.channel_reactions import render_early_feedback

    out = render_early_feedback(tool_ref=None, room_id="", message_id="")
    assert "react_to_user_message" not in out
    assert "emoji options" not in out
    assert "on it, one moment" in out


def test_render_early_feedback_inline_lark():
    from xyz_agent_context.channel.channel_reactions import render_early_feedback

    out = render_early_feedback(
        tool_ref="mcp__lark_module__react_to_user_message",
        room_id="oc", message_id="om", inline=True,
    )
    assert out.startswith("**Early feedback**:")
    assert "### Early feedback" not in out


class _FakeTelegramClientReject:
    last: "_FakeTelegramClientReject | None" = None

    def __init__(self, token):
        _FakeTelegramClientReject.last = self
        self.closed = False

    async def set_message_reaction(self, chat_id, message_id, emoji):
        return False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_telegram_react_rejected_returns_failure(monkeypatch):
    import xyz_agent_context.module.telegram_module._telegram_mcp_tools as m

    async def _cred(_a):
        return _Cred()

    monkeypatch.setattr(m, "_get_credential", _cred)
    monkeypatch.setattr(m, "TelegramSDKClient", _FakeTelegramClientReject)
    react = _tools(m.register_telegram_mcp_tools)["react_to_user_message"]
    out = await react("agent_a", "999", "7", "on_it")
    assert out["success"] is False
    assert _FakeTelegramClientReject.last.closed is True  # client still closed


@pytest.mark.asyncio
async def test_discord_sdk_add_reaction_rejects_non_snowflake():
    from xyz_agent_context.module.discord_module.discord_sdk_client import (
        DiscordSDKClient,
        DiscordSDKError,
    )

    client = DiscordSDKClient("tok")
    with pytest.raises(DiscordSDKError):
        await client.add_reaction("not/an/id", "456", "✅")
