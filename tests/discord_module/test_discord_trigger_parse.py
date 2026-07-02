"""
@file_name: test_discord_trigger_parse.py
@date: 2026-06-16
@description: Unit tests for DiscordTrigger parsing / gating / echo logic.

Pure-function tests — no Gateway connection. Covers:
  - _message_to_raw normalization from a discord.Message-like object
  - parse_event reply policy (DM passes, guild requires @-mention)
  - bot-author drop (loop guard) + empty-message drop
  - attachment → content_type derivation
  - is_echo by bot_user_id
  - extract_output scraping discord_send / discord_reply tool calls
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.module.discord_module._discord_credential_manager import (
    DiscordCredential,
)
from xyz_agent_context.module.discord_module.discord_trigger import DiscordTrigger
from xyz_agent_context.schema.parsed_message import ChatType, MessageContentType


def _cred(bot_user_id: str = "BOT1") -> DiscordCredential:
    return DiscordCredential(
        agent_id="agent_a", bot_token="MTA.tok", bot_user_id=bot_user_id, bot_username="acme"
    )


def _fake_message(
    *,
    msg_id="m1",
    channel_id="c1",
    guild_id="g1",
    author_id="u1",
    author_name="Alice",
    author_bot=False,
    content="hello",
    mentions=None,
    attachments=None,
    reference_id=None,
):
    """Build a discord.Message-like object for _message_to_raw."""
    guild = SimpleNamespace(id=guild_id) if guild_id else None
    author = SimpleNamespace(
        id=int(author_id) if author_id.isdigit() else author_id,
        name=author_name,
        display_name=author_name,
        bot=author_bot,
    )
    mention_objs = [SimpleNamespace(id=m) for m in (mentions or [])]
    att_objs = [
        SimpleNamespace(
            id=a["id"], url=a["url"], filename=a["filename"],
            content_type=a.get("content_type", ""), size=a.get("size", 0),
        )
        for a in (attachments or [])
    ]
    ref = SimpleNamespace(message_id=reference_id) if reference_id else None
    created = SimpleNamespace(timestamp=lambda: 1_700_000_000.0)
    return SimpleNamespace(
        id=msg_id,
        channel=SimpleNamespace(id=channel_id),
        guild=guild,
        author=author,
        mentions=mention_objs,
        attachments=att_objs,
        reference=ref,
        created_at=created,
        content=content,
    )


# ── _message_to_raw ────────────────────────────────────────────────────


def test_message_to_raw_dm_and_mention_detection():
    bot = SimpleNamespace(id=999)
    # guild message that @-mentions the bot
    msg = _fake_message(guild_id="g1", mentions=[999, 5])
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["is_dm"] is False
    assert raw["mentions_me"] is True
    assert "999" in raw["mentioned_ids"]

    # DM (no guild), no mention
    dm = _fake_message(guild_id="", mentions=[])
    raw_dm = DiscordTrigger._message_to_raw(dm, bot)
    assert raw_dm["is_dm"] is True
    assert raw_dm["mentions_me"] is False


def test_message_to_raw_extracts_attachments_and_reference():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(
        attachments=[{"id": "a1", "url": "https://cdn/x.png", "filename": "x.png", "content_type": "image/png", "size": 10}],
        reference_id="parent1",
    )
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["attachment_refs"][0]["url"] == "https://cdn/x.png"
    assert raw["attachment_refs"][0]["mime_hint"] == "image/png"
    assert raw["reference_id"] == "parent1"


# ── bot self-mention stripping (channel @mention → clean content) ──────
#
# Regression: a guild "@bot hi" arrives as raw markup "<@BOTID> hi". The
# opaque numeric mention is noise the model cannot resolve to "this is me",
# which degraded channel replies (DMs, with no such prefix, worked). We strip
# the bot's OWN mention so channel content matches the DM shape.


def test_strips_leading_bot_mention():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="g1", mentions=[999], content="<@999> hi")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"] == "hi"
    assert raw["mentions_me"] is True  # detection still works after strip


def test_strips_nickname_form_bot_mention():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="g1", mentions=[999], content="<@!999> hello there")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"] == "hello there"


def test_strips_trailing_and_inner_bot_mention():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="g1", mentions=[999], content="hey <@999> you")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"] == "hey you"


def test_preserves_other_user_mentions():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="g1", mentions=[999, 5], content="<@999> ping <@5>")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"] == "ping <@5>"  # only the bot's own mention is removed


def test_bare_bot_mention_not_blanked():
    # A bare "@bot" ping with no other text must NOT become empty — an empty
    # content is dropped by the trigger's empty guard, which would make the
    # bot ignore a direct ping. Keep the original so the agent still runs.
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="g1", mentions=[999], content="<@999>")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"].strip() != ""


def test_dm_content_unchanged_by_strip():
    bot = SimpleNamespace(id=999)
    msg = _fake_message(guild_id="", mentions=[], content="just hi")
    raw = DiscordTrigger._message_to_raw(msg, bot)
    assert raw["content"] == "just hi"


def test_strip_noop_when_bot_user_missing():
    # bot_user can be None before the gateway READY — don't crash, don't strip.
    msg = _fake_message(guild_id="g1", mentions=[999], content="<@999> hi")
    raw = DiscordTrigger._message_to_raw(msg, None)
    assert raw["content"] == "<@999> hi"


# ── parse_event reply policy ───────────────────────────────────────────


def _trigger() -> DiscordTrigger:
    return DiscordTrigger(max_workers=1)


def test_parse_dm_passes():
    raw = {"is_dm": True, "author_id": "u1", "message_id": "m1", "channel_id": "c1", "content": "hi", "author_name": "Alice"}
    pm = _trigger().parse_event(raw)
    assert pm is not None
    assert pm.chat_type == ChatType.PRIVATE
    assert pm.content == "hi"


def test_parse_guild_mention_passes():
    raw = {"is_dm": False, "mentions_me": True, "author_id": "u1", "message_id": "m1", "channel_id": "c1", "content": "hey bot", "author_name": "Alice"}
    pm = _trigger().parse_event(raw)
    assert pm is not None
    assert pm.chat_type == ChatType.GROUP


def test_parse_guild_without_mention_dropped():
    raw = {"is_dm": False, "mentions_me": False, "author_id": "u1", "message_id": "m1", "channel_id": "c1", "content": "random chatter"}
    assert _trigger().parse_event(raw) is None


def test_parse_bot_author_dropped():
    raw = {"is_dm": True, "author_is_bot": True, "author_id": "u1", "message_id": "m1", "channel_id": "c1", "content": "hi"}
    assert _trigger().parse_event(raw) is None


def test_parse_empty_no_attachment_dropped():
    raw = {"is_dm": True, "author_id": "u1", "message_id": "m1", "channel_id": "c1", "content": ""}
    assert _trigger().parse_event(raw) is None


def test_parse_empty_with_attachment_passes_and_sets_content_type():
    raw = {
        "is_dm": True, "author_id": "u1", "message_id": "m1", "channel_id": "c1",
        "content": "",
        "attachment_refs": [{"url": "https://cdn/x.png", "mime_hint": "image/png", "platform_ref": "a1"}],
    }
    pm = _trigger().parse_event(raw)
    assert pm is not None
    assert pm.content_type == MessageContentType.IMAGE


# ── is_echo ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_echo_matches_bot_user_id():
    trig = _trigger()
    raw = {"is_dm": True, "author_id": "BOT1", "message_id": "m1", "channel_id": "c1", "content": "hi", "author_name": "self"}
    pm = trig.parse_event(raw)
    assert pm is not None
    assert await trig.is_echo(pm, _cred(bot_user_id="BOT1")) is True
    assert await trig.is_echo(pm, _cred(bot_user_id="OTHER")) is False


# ── extract_output ─────────────────────────────────────────────────────


def test_extract_output_scrapes_send_and_reply():
    trig = _trigger()
    result = SimpleNamespace(
        raw_items=[
            {"item": {"type": "tool_call_item", "tool_name": "discord_reply", "arguments": {"text": "first"}}},
            {"item": {"type": "tool_call_item", "tool_name": "discord_send", "arguments": {"text": "second"}}},
            {"item": {"type": "tool_call_item", "tool_name": "discord_read_history", "arguments": {}}},
        ]
    )
    out = trig.extract_output(result, None, _cred())
    assert "first" in out and "second" in out


def test_extract_output_silent_when_no_send():
    trig = _trigger()
    result = SimpleNamespace(raw_items=[])
    assert trig.extract_output(result, None, _cred()) == "(stayed silent)"
