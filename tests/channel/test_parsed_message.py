"""
ParsedMessage smoke tests — defaults, enum behaviour, JSON-friendliness.
"""
from __future__ import annotations

import json

from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def test_parsed_message_defaults_match_contract():
    msg = ParsedMessage(message_id="m1", chat_id="c1", sender_id="u1")
    assert msg.sender_name == "Unknown"
    assert msg.content == ""
    assert msg.content_type is MessageContentType.TEXT
    assert msg.chat_type is ChatType.PRIVATE
    assert msg.timestamp_ms == 0
    assert msg.reply_to_message_id is None
    assert msg.thread_id is None
    assert msg.mentions == []
    assert msg.media_urls == []
    assert msg.raw == {}


def test_message_content_type_is_str_mixin():
    """str-mixin keeps json.dumps happy without a custom encoder."""
    payload = {"type": MessageContentType.IMAGE, "chat": ChatType.GROUP}
    encoded = json.dumps(payload)
    assert '"image"' in encoded
    assert '"group"' in encoded


def test_parsed_message_factory_default_lists_are_independent():
    """Two ParsedMessages must not share their default mutable lists/dicts."""
    a = ParsedMessage(message_id="a", chat_id="c", sender_id="u")
    b = ParsedMessage(message_id="b", chat_id="c", sender_id="u")
    a.mentions.append("u_other")
    a.media_urls.append("http://example.com")
    a.raw["sentinel"] = True
    assert b.mentions == []
    assert b.media_urls == []
    assert b.raw == {}
