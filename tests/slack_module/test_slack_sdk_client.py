"""
@file_name: test_slack_sdk_client.py
@date: 2026-05-08
@description: Tests for SlackSDKClient — the only file in the package
that imports slack_sdk directly.

Why this file exists:
    The wrapper has two contracts: (1) successful calls return plain
    dicts, (2) Slack's SlackApiError is translated into either a
    SlackSDKError (raise) or an envelope-style {"ok": false, ...}
    (return). Mocking AsyncWebClient lets us cover both branches
    without network.
"""
from __future__ import annotations

import pytest
from slack_sdk.errors import SlackApiError

from xyz_agent_context.module.slack_module import slack_sdk_client as sdk_mod
from xyz_agent_context.module.slack_module.slack_sdk_client import (
    SlackSDKClient,
    SlackSDKError,
)


class _FakeResponse:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeAsyncWebClient:
    """Configurable stand-in for slack_sdk.web.async_client.AsyncWebClient."""

    def __init__(self, token: str = ""):
        self.token = token
        # Per-method behaviour overrides — set from tests
        self.auth_test_response: dict | Exception | None = None
        self.chat_post_response: dict | Exception | None = None
        self.users_info_response: dict | Exception | None = None
        self.history_response: dict | Exception | None = None
        self.replies_response: dict | Exception | None = None
        self.api_call_response: dict | Exception | None = None
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _fake_api_error(code: str) -> SlackApiError:
        return SlackApiError(message=code, response={"ok": False, "error": code})

    async def _resolve(self, kind: str, override) -> _FakeResponse:
        if isinstance(override, Exception):
            raise override
        return _FakeResponse(override or {})

    async def auth_test(self):
        self.calls.append(("auth_test", {}))
        return await self._resolve("auth_test", self.auth_test_response)

    async def chat_postMessage(self, **kwargs):
        self.calls.append(("chat_postMessage", kwargs))
        return await self._resolve("chat_postMessage", self.chat_post_response)

    async def users_info(self, **kwargs):
        self.calls.append(("users_info", kwargs))
        return await self._resolve("users_info", self.users_info_response)

    async def conversations_history(self, **kwargs):
        self.calls.append(("conversations_history", kwargs))
        return await self._resolve("conversations_history", self.history_response)

    async def conversations_replies(self, **kwargs):
        self.calls.append(("conversations_replies", kwargs))
        return await self._resolve("conversations_replies", self.replies_response)

    async def api_call(self, method, json=None):
        self.calls.append(("api_call", {"method": method, "json": json}))
        return await self._resolve("api_call", self.api_call_response)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncWebClient:
    fake = _FakeAsyncWebClient()
    monkeypatch.setattr(sdk_mod, "AsyncWebClient", lambda token: fake)
    return fake


def test_constructor_rejects_empty_token():
    with pytest.raises(ValueError):
        SlackSDKClient("")


@pytest.mark.asyncio
async def test_auth_test_returns_response_data(fake_client):
    fake_client.auth_test_response = {
        "ok": True,
        "team_id": "T1",
        "team": "Acme",
        "user_id": "U0BOT",
    }
    client = SlackSDKClient("xoxb-x")
    out = await client.auth_test()
    assert out["team_id"] == "T1"
    assert out["user_id"] == "U0BOT"


@pytest.mark.asyncio
async def test_auth_test_raises_slack_sdk_error_on_api_error(fake_client):
    fake_client.auth_test_response = _FakeAsyncWebClient._fake_api_error("invalid_auth")
    client = SlackSDKClient("xoxb-x")
    with pytest.raises(SlackSDKError) as exc:
        await client.auth_test()
    assert exc.value.code == "invalid_auth"


@pytest.mark.asyncio
async def test_send_message_returns_ts(fake_client):
    fake_client.chat_post_response = {"ok": True, "ts": "1700000000.000100"}
    client = SlackSDKClient("xoxb-x")

    out = await client.send_message(channel="C1", text="hi", thread_ts=None)
    assert out["ts"] == "1700000000.000100"

    # Verify args reached upstream
    name, kwargs = fake_client.calls[0]
    assert name == "chat_postMessage"
    assert kwargs["channel"] == "C1"
    assert kwargs["text"] == "hi"


@pytest.mark.asyncio
async def test_send_message_raises_on_api_error(fake_client):
    fake_client.chat_post_response = _FakeAsyncWebClient._fake_api_error(
        "channel_not_found"
    )
    client = SlackSDKClient("xoxb-x")
    with pytest.raises(SlackSDKError) as exc:
        await client.send_message(channel="CBAD", text="hi")
    assert exc.value.code == "channel_not_found"


@pytest.mark.asyncio
async def test_get_user_info_returns_user_subobject(fake_client):
    fake_client.users_info_response = {
        "ok": True,
        "user": {"id": "U1", "real_name": "Ada"},
    }
    client = SlackSDKClient("xoxb-x")
    user = await client.get_user_info("U1")
    assert user == {"id": "U1", "real_name": "Ada"}


@pytest.mark.asyncio
async def test_get_user_info_returns_empty_dict_on_missing_user(fake_client):
    fake_client.users_info_response = {"ok": True}  # no "user" key
    client = SlackSDKClient("xoxb-x")
    assert await client.get_user_info("UGHOST") == {}


@pytest.mark.asyncio
async def test_get_user_info_swallows_api_errors(fake_client):
    fake_client.users_info_response = _FakeAsyncWebClient._fake_api_error("user_not_found")
    client = SlackSDKClient("xoxb-x")
    assert await client.get_user_info("UGHOST") == {}


@pytest.mark.asyncio
async def test_api_call_wraps_slack_api_error_into_envelope(fake_client):
    fake_client.api_call_response = _FakeAsyncWebClient._fake_api_error(
        "missing_scope"
    )
    client = SlackSDKClient("xoxb-x")
    out = await client.api_call("chat.postMessage", {"channel": "C1", "text": "x"})

    assert out["ok"] is False
    assert out["error"] == "missing_scope"
    assert out["method"] == "chat.postMessage"


@pytest.mark.asyncio
async def test_api_call_returns_response_data_on_success(fake_client):
    fake_client.api_call_response = {"ok": True, "channel": "C1"}
    client = SlackSDKClient("xoxb-x")
    out = await client.api_call("conversations.info", {"channel": "C1"})
    assert out == {"ok": True, "channel": "C1"}


@pytest.mark.asyncio
async def test_history_returns_messages_list(fake_client):
    fake_client.history_response = {
        "ok": True,
        "messages": [{"ts": "1.0", "text": "a"}, {"ts": "2.0", "text": "b"}],
    }
    client = SlackSDKClient("xoxb-x")
    out = await client.get_conversation_history(channel="C1", limit=5)
    assert len(out) == 2
    assert out[0]["text"] == "a"


@pytest.mark.asyncio
async def test_history_returns_empty_on_api_error(fake_client):
    fake_client.history_response = _FakeAsyncWebClient._fake_api_error("not_in_channel")
    client = SlackSDKClient("xoxb-x")
    assert await client.get_conversation_history(channel="C1") == []


# ── Sanitiser wiring ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_sanitises_markdown_link(fake_client):
    """``send_message`` must rewrite GitHub markdown to Slack mrkdwn."""
    fake_client.chat_post_response = {"ok": True, "ts": "1.0"}
    client = SlackSDKClient("xoxb-x")

    await client.send_message(
        channel="C1", text="see [docs](https://example.com)"
    )
    _, kwargs = fake_client.calls[0]
    assert kwargs["text"] == "see <https://example.com|docs>"


@pytest.mark.asyncio
async def test_send_message_sanitises_cjk_adjacent_url(fake_client):
    """``send_message`` must wrap a bare URL adjacent to CJK punct."""
    fake_client.chat_post_response = {"ok": True, "ts": "1.0"}
    client = SlackSDKClient("xoxb-x")

    await client.send_message(channel="C1", text="访问 https://example.com，详细")
    _, kwargs = fake_client.calls[0]
    assert kwargs["text"] == "访问 <https://example.com>，详细"


@pytest.mark.asyncio
async def test_api_call_sanitises_text_on_chat_postMessage(fake_client):
    """The agent-facing path (``slack_cli`` → ``api_call``) must also
    sanitise. Same fix, different entry point."""
    fake_client.api_call_response = {"ok": True, "ts": "1.0"}
    client = SlackSDKClient("xoxb-x")

    await client.api_call(
        "chat.postMessage",
        {"channel": "C1", "text": "[link](https://example.com)，详情"},
    )
    _, kwargs = fake_client.calls[0]
    sent = kwargs["json"]
    assert sent["text"] == "<https://example.com|link>，详情"


@pytest.mark.asyncio
async def test_api_call_sanitises_text_on_chat_update(fake_client):
    fake_client.api_call_response = {"ok": True}
    client = SlackSDKClient("xoxb-x")

    await client.api_call(
        "chat.update",
        {"channel": "C1", "ts": "1.0", "text": "new https://x.com，更新"},
    )
    _, kwargs = fake_client.calls[0]
    assert kwargs["json"]["text"] == "new <https://x.com>，更新"


@pytest.mark.asyncio
async def test_api_call_does_NOT_mutate_non_text_methods(fake_client):
    """``conversations.history`` and similar methods pass ``args``
    through verbatim — sanitiser must not run on irrelevant fields."""
    fake_client.api_call_response = {"ok": True, "messages": []}
    client = SlackSDKClient("xoxb-x")

    args = {"channel": "C1", "limit": 20}
    await client.api_call("conversations.history", args)
    _, kwargs = fake_client.calls[0]
    assert kwargs["json"] == args


@pytest.mark.asyncio
async def test_api_call_does_NOT_mutate_caller_args_dict(fake_client):
    """Sanitisation must operate on a copy. The caller passed-in dict
    is shared (e.g. tests assert on it, or the MCP layer logs it),
    so we must not rewrite it in place."""
    fake_client.api_call_response = {"ok": True, "ts": "1.0"}
    client = SlackSDKClient("xoxb-x")

    original_text = "see [docs](https://example.com)"
    args = {"channel": "C1", "text": original_text}
    await client.api_call("chat.postMessage", args)
    # Caller's dict unchanged
    assert args["text"] == original_text


@pytest.mark.asyncio
async def test_api_call_with_missing_text_field_is_passthrough(fake_client):
    """``chat.postMessage`` with blocks-only (no text) must not crash."""
    fake_client.api_call_response = {"ok": True, "ts": "1.0"}
    client = SlackSDKClient("xoxb-x")

    await client.api_call(
        "chat.postMessage",
        {"channel": "C1", "blocks": [{"type": "section"}]},
    )
    _, kwargs = fake_client.calls[0]
    assert "text" not in kwargs["json"]
