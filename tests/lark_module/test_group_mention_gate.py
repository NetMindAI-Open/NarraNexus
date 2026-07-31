"""
@file_name: test_group_mention_gate.py
@date: 2026-07-29
@description: The group-room @-mention gate — read every group message,
reply only when addressed.

Context: once the app holds `im:message.group_msg` (Lark's sensitive
"read all group messages" scope), the WS subscriber receives EVERY message
in every group the bot belongs to. Without this gate each one wakes an
agent_loop and the bot barges into unrelated conversation — observed in
production, then patched at the prompt layer, which is probabilistic and
the wrong layer for what is really channel semantics (binding rule #4).

The gate is deliberately fail-open on ambiguity: replying to one extra
message is recoverable, a bot that has gone mute in a group is not.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger
from xyz_agent_context.module.lark_module.lark_context_builder import (
    LarkContextBuilder,
)


BOT_OPEN_ID = "ou_bot_self"


def _cred(bot_open_id: str = BOT_OPEN_ID, bot_name: str = "NexusBot"):
    return SimpleNamespace(
        agent_id="agent_test",
        app_id="cli_test",
        brand="lark",
        bot_open_id=bot_open_id,
        bot_name=bot_name,
    )


def _event(chat_type: str = "group", mentions=None):
    return {
        "chat_type": chat_type,
        "chat_id": "oc_room",
        "message_id": "om_1",
        "mentions": mentions if mentions is not None else [],
    }


# ───────────────────── direct messages are never gated ─────────────────────

@pytest.mark.parametrize("chat_type", ["p2p", ""])
def test_direct_messages_always_warranted(chat_type):
    assert LarkTrigger.is_group_reply_warranted(_cred(), _event(chat_type)) is True


def test_missing_chat_type_defaults_to_direct():
    """A malformed event must not silence a 1:1 conversation."""
    assert LarkTrigger.is_group_reply_warranted(_cred(), {"message_id": "om_x"}) is True


# ───────────────────────────── group gating ────────────────────────────────

def test_group_without_mentions_is_skipped():
    assert LarkTrigger.is_group_reply_warranted(_cred(), _event()) is False


def test_group_mentioning_someone_else_is_skipped():
    ev = _event(mentions=[{"open_id": "ou_someone", "name": "Alice"}])
    assert LarkTrigger.is_group_reply_warranted(_cred(), ev) is False


def test_group_mentioning_bot_open_id_is_warranted():
    ev = _event(mentions=[
        {"open_id": "ou_someone", "name": "Alice"},
        {"open_id": BOT_OPEN_ID, "name": "NexusBot"},
    ])
    assert LarkTrigger.is_group_reply_warranted(_cred(), ev) is True


def test_open_id_wins_over_a_colliding_display_name():
    """A human renamed to the bot's display name must not trigger it."""
    ev = _event(mentions=[{"open_id": "ou_impostor", "name": "NexusBot"}])
    assert LarkTrigger.is_group_reply_warranted(_cred(), ev) is False


# ───────────────── legacy bindings without bot_open_id ─────────────────────

def test_name_fallback_when_open_id_not_stored():
    ev = _event(mentions=[{"open_id": "ou_x", "name": "NexusBot"}])
    assert LarkTrigger.is_group_reply_warranted(_cred(bot_open_id=""), ev) is True


def test_name_fallback_negative_is_respected():
    ev = _event(mentions=[{"open_id": "ou_x", "name": "Alice"}])
    assert LarkTrigger.is_group_reply_warranted(_cred(bot_open_id=""), ev) is False


def test_unknown_bot_identity_fails_open():
    """Neither id nor name stored: answer rather than go mute."""
    ev = _event(mentions=[{"open_id": "ou_x", "name": "Alice"}])
    cred = _cred(bot_open_id="", bot_name="")
    assert LarkTrigger.is_group_reply_warranted(cred, ev) is True


def test_unknown_identity_still_skips_when_nobody_mentioned():
    """Fail-open covers 'which mention is me', not 'was anyone mentioned'."""
    cred = _cred(bot_open_id="", bot_name="")
    assert LarkTrigger.is_group_reply_warranted(cred, _event()) is False


# ───────────────────────── SDK mention extraction ──────────────────────────

def test_extract_mentions_flattens_sdk_objects():
    message = SimpleNamespace(mentions=[
        SimpleNamespace(key="@_user_1", name="NexusBot",
                        id=SimpleNamespace(open_id=BOT_OPEN_ID)),
    ])
    assert LarkTrigger._extract_mentions(message) == [
        {"key": "@_user_1", "name": "NexusBot", "open_id": BOT_OPEN_ID},
    ]


@pytest.mark.parametrize("message", [
    SimpleNamespace(mentions=None),
    SimpleNamespace(mentions=[]),
    SimpleNamespace(),  # attribute absent entirely
])
def test_extract_mentions_handles_absent_lists(message):
    assert LarkTrigger._extract_mentions(message) == []


def test_extract_mentions_survives_missing_id_object():
    """`id` can be None on a partially-populated SDK payload."""
    message = SimpleNamespace(mentions=[
        SimpleNamespace(key="@_user_1", name="Alice", id=None),
    ])
    assert LarkTrigger._extract_mentions(message) == [
        {"key": "@_user_1", "name": "Alice", "open_id": ""},
    ]


# ─────────────────── group rooms get the read-history nudge ────────────────

@pytest.mark.asyncio
async def test_group_reply_instruction_tells_agent_to_read_history():
    builder = LarkContextBuilder(
        event={"chat_type": "group", "chat_id": "oc_room"},
        credential=_cred(),
        cli=None,
        agent_id="agent_test",
    )
    info = await builder.get_message_info()

    assert info["room_type"] == "Group Room"
    instruction = info["reply_instruction"]
    assert "im +chat-messages-list" in instruction
    assert "BEFORE" in instruction
    # Placeholders must be substituted, not leaked verbatim.
    assert "{chat_id}" not in instruction and "{agent_id}" not in instruction
    assert "oc_room" in instruction


@pytest.mark.asyncio
async def test_direct_message_instruction_has_no_group_nudge():
    builder = LarkContextBuilder(
        event={"chat_type": "p2p", "chat_id": "oc_dm"},
        credential=_cred(),
        cli=None,
        agent_id="agent_test",
    )
    info = await builder.get_message_info()

    assert info["room_type"] == "Direct Message"
    assert "im +chat-messages-list" not in info["reply_instruction"]
