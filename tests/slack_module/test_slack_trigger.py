"""
@file_name: test_slack_trigger.py
@date: 2026-05-08
@description: Tests for SlackTrigger event parsing + identity helpers.

Why this file exists:
    SlackTrigger sits between Slack's Socket Mode and the channel
    pipeline. The hot decisions — "is this an event we care about?",
    "is it our own bot's echo?", "what's the human display name?" —
    all live in narrow, pure-ish methods that are easy to cover
    without standing up a real Socket Mode session.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from xyz_agent_context.module.slack_module import slack_trigger as st_mod
from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredential,
)
from xyz_agent_context.module.slack_module.slack_sdk_client import SlackSDKError
from xyz_agent_context.module.slack_module.slack_trigger import SlackTrigger
from xyz_agent_context.schema.parsed_message import ChatType


def _cred(bot_user_id: str = "U0BOT") -> SlackCredential:
    return SlackCredential(
        agent_id="agent_a",
        bot_token="xoxb-test",
        app_token="xapp-test",
        bot_user_id=bot_user_id,
        team_id="T1",
        team_name="Team",
    )


class _StubSDK:
    """Replacement for SlackSDKClient for sender-name resolution."""

    def __init__(self, bot_token: str, *, user_payload: dict | None = None):
        self._user_payload = user_payload or {}
        self.calls: list[str] = []

    async def get_user_info(self, user_id: str) -> dict:
        self.calls.append(user_id)
        return self._user_payload


def test_instantiates_without_db():
    trigger = SlackTrigger(max_workers=3)
    assert trigger.channel_name == "slack"
    assert trigger.brand_display == "Slack"
    assert trigger._socket_clients == {}


# ── parse_event ─────────────────────────────────────────────────────────


def test_parse_event_message_returns_parsed_message():
    """Regular ``message`` events are accepted in DM context (im)."""
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "user": "U123",
        "channel": "D456",
        "channel_type": "im",
        "text": "hello world",
        "ts": "1700000000.000100",
        "client_msg_id": "msg-uuid-1",
    }
    parsed = trigger.parse_event(raw)

    assert parsed is not None
    assert parsed.message_id == "msg-uuid-1"
    assert parsed.sender_id == "U123"
    assert parsed.chat_id == "D456"
    assert parsed.content == "hello world"
    assert parsed.timestamp_ms == 1700000000000  # ts * 1000
    assert parsed.chat_type == ChatType.PRIVATE  # D-prefixed DM
    assert parsed.thread_id is None


def test_parse_event_app_mention_accepted():
    trigger = SlackTrigger()
    raw = {
        "type": "app_mention",
        "user": "U999",
        "channel": "C100",
        "text": "<@U0BOT> ping",
        "ts": "1700000001.000000",
    }
    parsed = trigger.parse_event(raw)

    assert parsed is not None
    assert parsed.sender_id == "U999"
    assert "U0BOT" in parsed.mentions


def test_parse_event_subtype_channel_join_returns_none():
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "subtype": "channel_join",
        "user": "U1",
        "channel": "C1",
        "ts": "1.0",
    }
    assert trigger.parse_event(raw) is None


def test_parse_event_message_changed_subtype_returns_none():
    trigger = SlackTrigger()
    assert (
        trigger.parse_event(
            {
                "type": "message",
                "subtype": "message_changed",
                "user": "U1",
                "channel": "C1",
                "ts": "1.0",
            }
        )
        is None
    )


def test_parse_event_missing_user_returns_none():
    """Tombstone-y events with no `user` should be skipped."""
    trigger = SlackTrigger()
    raw = {
        "type": "message",
        "channel": "C1",
        "ts": "1.0",
    }
    assert trigger.parse_event(raw) is None


def test_parse_event_preserves_thread_ts():
    """thread_ts must propagate to ``ParsedMessage.thread_id``. Uses DM
    context (channel_type=im) so the Phase 5 filter doesn't drop the
    event — separately, thread replies in channels arrive as
    ``app_mention`` which is filter-exempt anyway."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D2",
            "channel_type": "im",
            "text": "in thread",
            "ts": "1700000002.000200",
            "thread_ts": "1700000000.000000",
        }
    )
    assert parsed is not None
    assert parsed.thread_id == "1700000000.000000"


def test_parse_event_dm_channel_classified_private():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D123",  # DM
            "channel_type": "im",
            "text": "secret",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert parsed.chat_type == ChatType.PRIVATE


def test_parse_event_falls_back_to_ts_when_no_client_msg_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U2",
            "channel": "D2",
            "channel_type": "im",
            "text": "hi",
            "ts": "1700000003.000300",
        }
    )
    assert parsed is not None
    assert parsed.message_id == "1700000003.000300"


# ── Phase 5: channel_type filter ────────────────────────────────────────


def test_parse_event_dm_message_accepted():
    """``channel_type='im'`` → accepted (1:1 DM)."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "D1",
            "channel_type": "im",
            "text": "hi",
            "ts": "1.0",
        }
    )
    assert parsed is not None


def test_parse_event_mpim_message_accepted():
    """``channel_type='mpim'`` → accepted (multi-party DM)."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "G1",
            "channel_type": "mpim",
            "text": "hi all",
            "ts": "1.0",
        }
    )
    assert parsed is not None


def test_parse_event_channel_message_dropped():
    """Public channel ``message`` event with no @-mention → dropped.

    Phase 5 reply policy: in channels we only honour ``app_mention``;
    everything else from ``channel_type='channel'`` is filtered out at the
    trigger boundary so the agent never sees it.
    """
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "C1",
            "channel_type": "channel",
            "text": "general chatter",
            "ts": "1.0",
        }
    )
    assert parsed is None


def test_parse_event_group_message_dropped():
    """Private channel (``channel_type='group'``) ``message`` event → dropped."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U1",
            "channel": "G1",
            "channel_type": "group",
            "text": "private chatter",
            "ts": "1.0",
        }
    )
    assert parsed is None


def test_parse_event_app_mention_in_channel_accepted():
    """``app_mention`` events are exempt from the channel_type filter.

    The whole point of Phase 5 is "only reply when @-mentioned in
    channels", and ``app_mention`` is precisely how Slack delivers an
    @-mention. It must still come through even though channel-message
    events do not.
    """
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "app_mention",
            "user": "U1",
            "channel": "C1",
            # No channel_type field — app_mention doesn't carry one and
            # the filter must not apply to this event type.
            "text": "<@U0BOT> ping",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert parsed.sender_id == "U1"


# ── is_echo ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_echo_detects_bot_user_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U0BOT",
            "channel": "D1",
            "channel_type": "im",
            "text": "from bot",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("U0BOT")) is True


@pytest.mark.asyncio
async def test_is_echo_false_for_human_sender():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "UHUMAN",
            "channel": "D1",
            "channel_type": "im",
            "text": "hi bot",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("U0BOT")) is False


@pytest.mark.asyncio
async def test_is_echo_false_when_credential_has_no_bot_user_id():
    trigger = SlackTrigger()
    parsed = trigger.parse_event(
        {
            "type": "message",
            "user": "U0BOT",
            "channel": "D1",
            "channel_type": "im",
            "text": "x",
            "ts": "1.0",
        }
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred("")) is False


# ── is_permanent_auth_failure ──────────────────────────────────────────
#
# Socket Mode's ``connect()`` is the ONE Slack call that does NOT go
# through our ``SlackSDKClient`` wrapper — it is invoked directly on the
# raw slack_sdk ``SocketModeClient``. So a dead app-level token surfaces
# as a *raw* ``slack_sdk.errors.SlackApiError`` (carrying
# ``{"ok": false, "error": "invalid_auth"}``), NOT our ``SlackSDKError``.
# The classifier must recognise both shapes, otherwise a permanently
# dead credential is misread as a transient blip and the base
# ``_subscribe_loop`` reconnects forever instead of disabling it.


def _api_error(code: str) -> SlackApiError:
    return SlackApiError(message=code, response={"ok": False, "error": code})


def test_permanent_auth_failure_raw_slack_api_error_invalid_auth():
    trigger = SlackTrigger()
    # This is exactly what Socket Mode's connect() raises for a dead app_token.
    assert trigger.is_permanent_auth_failure(_api_error("invalid_auth")) is True


def test_permanent_auth_failure_raw_slack_api_error_token_revoked():
    trigger = SlackTrigger()
    assert trigger.is_permanent_auth_failure(_api_error("token_revoked")) is True


def test_transient_raw_slack_api_error_is_not_permanent():
    trigger = SlackTrigger()
    # ``ratelimited`` (Slack's real transient code) MUST keep reconnecting —
    # disabling a healthy credential on a blip is the failure we must not
    # introduce. (issue_new_wss_url() actually retries ratelimited itself,
    # but the classifier must still not mark it permanent if it ever bubbles.)
    assert trigger.is_permanent_auth_failure(_api_error("ratelimited")) is False


def test_raw_slack_api_error_without_response_is_not_permanent():
    trigger = SlackTrigger()
    err = SlackApiError(message="boom", response=None)  # type: ignore[arg-type]
    assert trigger.is_permanent_auth_failure(err) is False


def test_permanent_auth_failure_wrapped_slack_sdk_error_still_works():
    trigger = SlackTrigger()
    # The wrapped path (files.info, auth.test, etc.) must keep working.
    assert trigger.is_permanent_auth_failure(SlackSDKError("invalid_auth")) is True
    assert trigger.is_permanent_auth_failure(SlackSDKError("channel_not_found")) is False


def test_permanent_auth_failure_unrelated_exception_is_false():
    trigger = SlackTrigger()
    assert trigger.is_permanent_auth_failure(RuntimeError("network hiccup")) is False


# ── connect() must let a dead token ESCAPE (propagation regression) ─────
#
# The classifier fix above is only reachable if the raw SlackApiError
# actually propagates out of connect(). slack_sdk's connect() swallows
# it in a `while True` retry loop, so SlackTrigger.connect() pre-fetches
# the WSS URL itself (issue_new_wss_url re-raises non-ratelimited errors)
# to force the error to bubble up to _subscribe_loop. Without that, this
# test hangs/never raises and the credential is never disabled.


class _FakeSocketClient:
    """Minimal stand-in for slack_sdk's SocketModeClient — issue_new_wss_url
    raises like a dead app_token; connect/close are inert AsyncMocks so the
    test can assert cleanup happened. Constructed instances register
    themselves in ``_FakeSocketClient.instances`` so the test can inspect
    the one connect() built internally."""

    instances: list = []

    def __init__(self, **_kwargs):
        self.socket_mode_request_listeners: list = []
        self.wss_uri = None
        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.close = AsyncMock()
        _FakeSocketClient.instances.append(self)

    async def issue_new_wss_url(self):
        raise _api_error("invalid_auth")


@pytest.mark.asyncio
async def test_connect_propagates_permanent_auth_error(monkeypatch: pytest.MonkeyPatch):
    trigger = SlackTrigger()
    _FakeSocketClient.instances = []
    monkeypatch.setattr(st_mod, "_HAS_SLACK_SOCKET", True)
    monkeypatch.setattr(st_mod, "SocketModeClient", _FakeSocketClient)
    monkeypatch.setattr(st_mod, "SocketModeResponse", object)
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: SimpleNamespace(web=object()))

    agen = trigger.connect(_cred())
    with pytest.raises(SlackApiError) as ei:
        await agen.__anext__()
    # And the base loop would classify it as permanent → disable.
    assert trigger.is_permanent_auth_failure(ei.value) is True
    # The half-constructed client (live ClientSession + process_messages
    # task in the real SDK) must be fully closed, not leaked — close(), not
    # disconnect().
    client = _FakeSocketClient.instances[-1]
    client.close.assert_awaited_once()
    client.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_loop_disables_credential_on_permanent_auth(
    monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end: connect() raising invalid_auth → base loop disables the
    credential exactly once and stops (no infinite reconnect)."""
    trigger = SlackTrigger()
    trigger._db = object()
    trigger.running = True

    async def _boom(_cred):
        # Flip running off so that IF this chain ever regresses (raise no
        # longer classified permanent → base loop takes the backoff branch)
        # the loop breaks instead of sleeping+retrying forever — a regression
        # then fails the assertion fast instead of hanging CI. In the healthy
        # path the permanent branch returns before running is even checked.
        trigger.running = False
        raise _api_error("invalid_auth")
        yield  # pragma: no cover — makes this an async generator

    disable = AsyncMock()
    monkeypatch.setattr(trigger, "connect", _boom)
    monkeypatch.setattr(trigger, "_on_transport_connected", AsyncMock())
    monkeypatch.setattr(trigger, "_audit", AsyncMock())
    monkeypatch.setattr(trigger, "disable_credential", disable)

    # Returns (does not loop forever) because permanent failure → disable → return.
    await trigger._subscribe_loop(_cred())

    disable.assert_awaited_once()


# ── resolve_sender_name ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_sender_name_uses_real_name(monkeypatch: pytest.MonkeyPatch):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={"real_name": "Ada Lovelace"})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    name = await trigger.resolve_sender_name("U42", _cred())
    assert name == "Ada Lovelace"
    assert stub.calls == ["U42"]


@pytest.mark.asyncio
async def test_resolve_sender_name_caches_result(monkeypatch: pytest.MonkeyPatch):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={"real_name": "Ada"})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    first = await trigger.resolve_sender_name("U42", _cred())
    second = await trigger.resolve_sender_name("U42", _cred())

    assert first == second == "Ada"
    # Cache hit on second call — only one upstream call.
    assert stub.calls == ["U42"]


@pytest.mark.asyncio
async def test_resolve_sender_name_falls_back_to_user_id(
    monkeypatch: pytest.MonkeyPatch,
):
    trigger = SlackTrigger()
    stub = _StubSDK("xoxb-test", user_payload={})
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda _t: stub)

    name = await trigger.resolve_sender_name("U_NONAME", _cred())
    assert name == "U_NONAME"


def test_extract_output_returns_silent_sentinel_for_empty_text():
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items: list = []

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_reads_slack_cli_args_not_output_text():
    """Inbox should show what was sent to Slack, not the agent's reasoning."""
    trigger = SlackTrigger()

    class _R:
        # Agent's reasoning — this MUST NOT end up in the inbox.
        output_text = "My thought process: User asked X, I should reply Y"
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": {
                        "agent_id": "agent_a",
                        "method": "chat.postMessage",
                        "args": {
                            "channel": "D0B2AR5MC3Z",
                            "text": "Hello, I'm here to help!",
                        },
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "Hello, I'm here to help!"
    assert "thought process" not in out


def test_extract_output_skips_non_chat_postMessage_calls():
    """reactions.add and friends are NOT user-visible reply text."""
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": {
                        "method": "reactions.add",
                        "args": {"channel": "C1", "timestamp": "1.0", "name": "thumbsup"},
                    },
                }
            }
        ]

    # No chat.postMessage call → "stayed silent" sentinel
    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_handles_args_as_json_string():
    """When `arguments` arrives as a serialized JSON string, parse it."""
    import json
    trigger = SlackTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__slack_module__slack_cli",
                    "arguments": json.dumps({
                        "method": "chat.postMessage",
                        "args": {"channel": "C1", "text": "string-encoded reply"},
                    }),
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "string-encoded reply"
