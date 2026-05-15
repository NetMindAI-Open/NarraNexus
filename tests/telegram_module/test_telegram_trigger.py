"""
@file_name: test_telegram_trigger.py
@date: 2026-05-09
@description: Tests for TelegramTrigger event parsing + extract_output.

Why this file exists:
    The trigger sits between Telegram's long-poll and the channel
    pipeline. Hot decisions — "is this an event we care about?", "is
    it our own bot's echo?", "what did the agent actually send?" —
    live in narrow methods that are easy to cover without standing
    up a real long-poll loop.

    The ``extract_output`` regression test mirrors the Phase 3 Slack
    bug: the inbox MUST show the bot's reply text (scraped from
    ``tg_cli`` sendMessage args), NOT ``result.output_text`` which
    leaks the agent's reasoning.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_trigger import (
    TelegramTrigger,
)
from xyz_agent_context.schema.parsed_message import ChatType


def _cred(bot_user_id: str = "1001", bot_username: str = "acme_bot") -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id=bot_user_id,
        bot_username=bot_username,
    )


def _msg(**overrides) -> dict:
    """Minimal Telegram update payload."""
    base = {
        "update_id": 100,
        "message": {
            "message_id": 7,
            "date": 1700000000,
            "from": {"id": 42, "first_name": "Ada", "last_name": "Lovelace"},
            "chat": {"id": 99, "type": "private"},
            "text": "hello",
        },
    }
    base["message"].update(overrides)
    return base


# ── parse_event ─────────────────────────────────────────────────────────


def test_parse_event_normal_text_message():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg())

    assert parsed is not None
    assert parsed.message_id == "7"
    assert parsed.chat_id == "99"
    assert parsed.sender_id == "42"
    assert parsed.sender_name == "Ada Lovelace"
    assert parsed.content == "hello"
    assert parsed.chat_type == ChatType.PRIVATE
    assert parsed.timestamp_ms == 1700000000 * 1000


def test_parse_event_no_text_returns_none():
    """Sticker / media / voice — Phase 4 is text-only."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(text="", sticker={"emoji": ":-)"})
    )
    assert parsed is None


def test_parse_event_with_message_thread_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg(message_thread_id=12345))

    assert parsed is not None
    assert parsed.thread_id == "12345"


def test_parse_event_with_reply_to_message():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(reply_to_message={"message_id": 6})
    )

    assert parsed is not None
    assert parsed.reply_to_message_id == "6"


def test_parse_event_with_mention_entity():
    trigger = TelegramTrigger()
    text = "hi @acme_bot please"
    parsed = trigger.parse_event(
        _msg(
            text=text,
            entities=[{"type": "mention", "offset": 3, "length": 9}],
        )
    )

    assert parsed is not None
    assert "acme_bot" in parsed.mentions


def test_parse_event_text_mention_uses_user_id():
    """Inline mention without @username — use the embedded user.id."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="poke",
            entities=[
                {
                    "type": "text_mention",
                    "offset": 0,
                    "length": 4,
                    "user": {"id": 555},
                }
            ],
        )
    )

    assert parsed is not None
    assert "555" in parsed.mentions


def test_parse_event_supergroup_chat_classified_group():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(chat={"id": -1001234567890, "type": "supergroup", "title": "Forum"})
    )

    assert parsed is not None
    assert parsed.chat_type == ChatType.GROUP
    assert parsed.chat_id == "-1001234567890"


def test_parse_event_no_message_returns_none():
    trigger = TelegramTrigger()
    assert trigger.parse_event({"update_id": 1}) is None


# ── is_echo ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_echo_detects_bot_user_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(**{"from": {"id": 1001, "first_name": "Bot"}})
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="1001")) is True


@pytest.mark.asyncio
async def test_is_echo_false_for_human_sender():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg())
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="1001")) is False


@pytest.mark.asyncio
async def test_is_echo_false_when_credential_has_no_bot_user_id():
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(**{"from": {"id": 1001, "first_name": "Bot"}})
    )
    assert parsed is not None
    assert await trigger.is_echo(parsed, _cred(bot_user_id="")) is False


# ── extract_output: PHASE-3 REGRESSION ────────────────────────────────


def test_extract_output_reads_tg_cli_args_not_output_text():
    """REGRESSION: inbox shows what was sent via tg_cli sendMessage,
    NOT ``result.output_text`` which leaks the agent's chain-of-thought
    (the Phase 3 Slack bug — must not recur here)."""
    trigger = TelegramTrigger()

    class _R:
        output_text = "My thought process: User asked X, I should reply Y"
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": {
                        "agent_id": "agent_a",
                        "method": "sendMessage",
                        "args": {"chat_id": "99", "text": "Hello, I'm here!"},
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "Hello, I'm here!"
    assert "thought process" not in out


def test_extract_output_returns_silent_sentinel_when_no_send_message():
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items: list = []

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_skips_non_send_message_calls():
    """setMessageReaction / deleteMessage / etc. are NOT user-visible reply text."""
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": {
                        "method": "setMessageReaction",
                        "args": {
                            "chat_id": "99",
                            "message_id": 7,
                            "reaction": [{"type": "emoji", "emoji": "👍"}],
                        },
                    },
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "(stayed silent)"


def test_extract_output_handles_arguments_as_json_string():
    """When ``arguments`` arrives serialized, parse it before scraping."""
    trigger = TelegramTrigger()

    class _R:
        output_text = ""
        raw_items = [
            {
                "item": {
                    "type": "tool_call_item",
                    "tool_name": "mcp__telegram_module__tg_cli",
                    "arguments": json.dumps(
                        {
                            "method": "sendMessage",
                            "args": {
                                "chat_id": "99",
                                "text": "string-encoded reply",
                            },
                        }
                    ),
                }
            }
        ]

    out = trigger.extract_output(_R(), None, _cred())
    assert out == "string-encoded reply"


# ── resolve_sender_name fallback ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_sender_name_returns_sender_id_fallback():
    """Telegram has no user-info-by-id endpoint without chat context —
    the fallback path returns the sender_id as a stable identifier."""
    trigger = TelegramTrigger()
    name = await trigger.resolve_sender_name("42", _cred())
    assert name == "42"


# ── Late owner resolution via _maybe_resolve_owner ─────────────────────


def _cred_pending(owner_username: str = "ctong201") -> TelegramCredential:
    """Credential representing a fresh bind: owner_username set (the lock)
    but owner_user_id still empty (getChat at bind couldn't resolve a
    user @handle — expected per Telegram API)."""
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
        owner_username=owner_username,
        owner_user_id="",
        owner_name="",
    )


def _make_parsed(raw: dict, sender_id: str = "8612707834"):
    """Helper — produce ParsedMessage with .raw + .sender_id matching
    what TelegramTrigger.parse_event would emit."""
    from xyz_agent_context.schema.parsed_message import ParsedMessage, MessageContentType, ChatType
    return ParsedMessage(
        message_id="7",
        chat_id="8612707834",
        sender_id=sender_id,
        sender_name="x",
        content="hi",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=0,
        raw=raw,
    )


@pytest.mark.asyncio
async def test_late_owner_resolution_fires_on_username_match(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """First DM whose from.username matches the stored owner_username
    should populate owner_user_id + owner_name. This is the Telegram-
    specific equivalent of Slack's bind-time users.lookupByEmail."""
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        TelegramCredentialManager,
    )

    # Pre-create the pending credential row in DB
    mgr = TelegramCredentialManager(db_client)
    # Bypass bind() to avoid the SDK call — directly insert via underlying
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        _encode_token,
    )
    await db_client.insert("channel_telegram_credentials", {
        "agent_id": "agent_a",
        "bot_token_encoded": _encode_token("1234:tok"),
        "bot_user_id": "1001",
        "bot_username": "acme_bot",
        "owner_username": "ctong201",
        "owner_user_id": "",
        "owner_name": "",
        "enabled": 1,
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
    })

    trigger = TelegramTrigger()
    trigger._db = db_client

    raw = _msg(**{
        "from": {
            "id": 8612707834,
            "username": "ctong201",  # matches lock
            "first_name": "Chen",
            "last_name": "Tong",
        }
    })
    message = _make_parsed(raw)
    credential = _cred_pending()

    await trigger._maybe_resolve_owner(credential, message)

    # In-memory mutation
    assert credential.owner_user_id == "8612707834"
    assert credential.owner_name == "Chen Tong"
    # And persisted
    after = await mgr.get("agent_a")
    assert after is not None
    assert after.owner_user_id == "8612707834"
    assert after.owner_name == "Chen Tong"


@pytest.mark.asyncio
async def test_late_owner_resolution_ignores_username_mismatch(
    db_client, monkeypatch: pytest.MonkeyPatch
):
    """SECURITY: a stranger DM'ing the bot first must NOT be able to
    claim owner. owner_username is the lock; only matching usernames
    unlock."""
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        TelegramCredentialManager, _encode_token,
    )

    await db_client.insert("channel_telegram_credentials", {
        "agent_id": "agent_a",
        "bot_token_encoded": _encode_token("1234:tok"),
        "bot_user_id": "1001",
        "bot_username": "acme_bot",
        "owner_username": "ctong201",
        "owner_user_id": "",
        "owner_name": "",
        "enabled": 1,
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
    })

    trigger = TelegramTrigger()
    trigger._db = db_client

    raw = _msg(**{
        "from": {
            "id": 9999999,
            "username": "random_stranger",  # does NOT match
            "first_name": "Eve",
        }
    })
    message = _make_parsed(raw, sender_id="9999999")
    credential = _cred_pending()

    await trigger._maybe_resolve_owner(credential, message)

    # No-op — stranger can't claim
    assert credential.owner_user_id == ""
    assert credential.owner_name == ""
    after = await TelegramCredentialManager(db_client).get("agent_a")
    assert after is not None
    assert after.owner_user_id == ""


@pytest.mark.asyncio
async def test_late_owner_resolution_case_insensitive(db_client):
    """Telegram usernames are case-preserving but case-insensitive at
    match time (@CTONG201 == @ctong201). The lock must match the same way."""
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        TelegramCredentialManager, _encode_token,
    )

    await db_client.insert("channel_telegram_credentials", {
        "agent_id": "agent_a",
        "bot_token_encoded": _encode_token("1234:tok"),
        "bot_user_id": "1001",
        "bot_username": "acme_bot",
        "owner_username": "ctong201",  # lowercase lock
        "owner_user_id": "",
        "owner_name": "",
        "enabled": 1,
        "created_at": "2026-05-11T00:00:00+00:00",
        "updated_at": "2026-05-11T00:00:00+00:00",
    })

    trigger = TelegramTrigger()
    trigger._db = db_client

    raw = _msg(**{
        "from": {
            "id": 8612707834,
            "username": "Ctong201",  # different case — still owner
            "first_name": "Chen",
        }
    })
    message = _make_parsed(raw)
    credential = _cred_pending()

    await trigger._maybe_resolve_owner(credential, message)

    assert credential.owner_user_id == "8612707834"


@pytest.mark.asyncio
async def test_late_owner_resolution_skips_when_no_owner_username(db_client):
    """If owner_username wasn't set at bind, there's no lock — late
    resolution must not fire, otherwise first-DM-wins becomes the
    de-facto policy (security regression)."""
    from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
        TelegramCredentialManager,
    )

    trigger = TelegramTrigger()
    trigger._db = db_client

    raw = _msg(**{"from": {"id": 8612707834, "username": "ctong201", "first_name": "Chen"}})
    message = _make_parsed(raw)
    credential = TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
        owner_username="",  # NO lock — no auto-resolve
    )

    await trigger._maybe_resolve_owner(credential, message)

    assert credential.owner_user_id == ""
    assert credential.owner_name == ""


@pytest.mark.asyncio
async def test_late_owner_resolution_skips_when_sender_has_no_username(db_client):
    """A Telegram user without a public @username has empty
    `from.username` in events. They can never satisfy the lock — must
    not be claimed as owner. (Edge case: users without public usernames
    need a different path, e.g. numeric user_id at bind. Phase 4 doesn't
    support them.)"""
    trigger = TelegramTrigger()
    trigger._db = db_client

    raw = _msg(**{"from": {"id": 8612707834, "first_name": "Chen"}})  # no username
    message = _make_parsed(raw)
    credential = _cred_pending()

    await trigger._maybe_resolve_owner(credential, message)

    assert credential.owner_user_id == ""
    assert credential.owner_name == ""


# ── processing_indicator — typing pump ──────────────────────────────────


class _StubTelegramClient:
    """Records every send_chat_action call. close() is a no-op."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def send_chat_action(self, chat_id, action: str = "typing") -> bool:
        self.calls.append((str(chat_id), action))
        return True

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_processing_indicator_fires_once_for_fast_agent_run():
    """Agent finishes faster than the refresh interval → one typing
    ping covers the whole window. Validates the "fire immediately on
    enter" contract — users see "typing..." within the latency budget,
    not 4s later."""
    trigger = TelegramTrigger()
    trigger.TYPING_REFRESH_SECONDS = 1.0  # speed up the loop for tests
    stub = _StubTelegramClient()
    trigger._sdk_clients[trigger._subscriber_key(_cred())] = stub  # type: ignore[assignment]

    msg = _make_parsed(_msg())
    async with trigger.processing_indicator(_cred(), msg):
        await asyncio.sleep(0.05)  # very short agent run

    assert len(stub.calls) == 1
    assert stub.calls[0] == (msg.chat_id, "typing")


@pytest.mark.asyncio
async def test_processing_indicator_refreshes_while_agent_runs():
    """Agent run spans multiple refresh windows → typing fires
    repeatedly. Without this the indicator would disappear after 5s,
    leaving the user staring at silence while the agent thinks."""
    trigger = TelegramTrigger()
    trigger.TYPING_REFRESH_SECONDS = 0.1  # ~10 ticks per second
    stub = _StubTelegramClient()
    trigger._sdk_clients[trigger._subscriber_key(_cred())] = stub  # type: ignore[assignment]

    msg = _make_parsed(_msg())
    async with trigger.processing_indicator(_cred(), msg):
        await asyncio.sleep(0.35)  # ≈ 3 refresh intervals

    assert len(stub.calls) >= 3, (
        f"Expected ≥3 typing pings during a 0.35s window with 0.1s refresh, "
        f"got {len(stub.calls)}."
    )


@pytest.mark.asyncio
async def test_processing_indicator_stops_promptly_on_exit():
    """When the agent finishes mid-interval, the pump MUST exit on
    stop_event rather than waiting out the full refresh window."""
    trigger = TelegramTrigger()
    trigger.TYPING_REFRESH_SECONDS = 5.0  # would block for 5s without proper signal
    stub = _StubTelegramClient()
    trigger._sdk_clients[trigger._subscriber_key(_cred())] = stub  # type: ignore[assignment]

    msg = _make_parsed(_msg())
    import time as _time
    t0 = _time.monotonic()
    async with trigger.processing_indicator(_cred(), msg):
        pass  # exit immediately
    elapsed = _time.monotonic() - t0

    # Should be well under the 5s refresh interval — stop_event MUST
    # short-circuit the asyncio.wait_for inside the pump.
    assert elapsed < 1.0, (
        f"processing_indicator took {elapsed:.2f}s to exit — pump is not "
        f"honouring stop_event; future agents will hang on cleanup."
    )


@pytest.mark.asyncio
async def test_processing_indicator_swallows_send_chat_action_errors():
    """sendChatAction failure (rate limit, chat gone, network) MUST NOT
    propagate out of the context manager — the indicator is cosmetic;
    the agent run inside the ``async with`` block must always run."""

    class _FailingClient:
        async def send_chat_action(self, chat_id, action="typing"):
            raise RuntimeError("simulated Telegram outage")

        async def close(self):
            return None

    trigger = TelegramTrigger()
    trigger.TYPING_REFRESH_SECONDS = 0.05
    trigger._sdk_clients[trigger._subscriber_key(_cred())] = _FailingClient()  # type: ignore[assignment]

    msg = _make_parsed(_msg())
    # If the pump leaked the exception, ``async with`` would re-raise.
    async with trigger.processing_indicator(_cred(), msg):
        await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_processing_indicator_falls_back_when_no_cached_client(
    monkeypatch: pytest.MonkeyPatch,
):
    """If `_sdk_clients` doesn't have a client for this credential
    (corner cases: tests, subscriber not yet spawned), the indicator
    MUST still fire — it just creates a short-lived client."""
    from xyz_agent_context.module.telegram_module import telegram_trigger as tt_mod

    stub = _StubTelegramClient()
    monkeypatch.setattr(tt_mod, "TelegramSDKClient", lambda _t: stub)

    trigger = TelegramTrigger()
    trigger.TYPING_REFRESH_SECONDS = 1.0
    # NOTE: do NOT seed _sdk_clients — force the fallback path.

    msg = _make_parsed(_msg())
    async with trigger.processing_indicator(_cred(), msg):
        await asyncio.sleep(0.05)

    assert len(stub.calls) == 1
