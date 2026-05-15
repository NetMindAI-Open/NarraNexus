"""
@file_name: parsed_message.py
@date: 2026-05-08
@description: Unified IM channel message format.

Every IM channel (Lark, Slack, Telegram, ...) parses its platform-specific
event into a ``ParsedMessage`` before entering the dedup → debounce →
worker pipeline owned by ``ChannelTriggerBase``. Inspired by hermes-agent's
MessageEvent, adapted to NarraNexus naming conventions.

Notes for channel implementers:
- ``message_id`` MUST be unique within the channel + chat scope; the dedup
  store keys on (channel, message_id).
- ``timestamp_ms`` is wall-clock milliseconds since epoch (the format Lark,
  Slack, Telegram all expose); the historic-replay filter relies on it.
- ``raw`` is a free-form pass-through dict; subclasses needing
  channel-specific fields downstream (e.g. Lark's ``sender_type``) stash
  them here without polluting the canonical struct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MessageContentType(str, Enum):
    """Coarse content classification — channel-agnostic."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    STICKER = "sticker"
    LOCATION = "location"


class ChatType(str, Enum):
    """Coarse chat-room classification — channel-agnostic."""
    PRIVATE = "private"          # 1:1 DM
    GROUP = "group"              # plain group chat
    TOPIC_GROUP = "topic_group"  # grouped/topic threading (e.g. Slack channel with threads)


@dataclass
class ParsedMessage:
    """
    Normalized incoming-message struct.

    All IM channel events are converted to this shape by
    ``ChannelTriggerBase`` subclasses' ``parse_event`` method before
    entering the pipeline.
    """

    message_id: str
    chat_id: str
    sender_id: str
    sender_name: str = "Unknown"
    content: str = ""
    content_type: MessageContentType = MessageContentType.TEXT
    chat_type: ChatType = ChatType.PRIVATE
    timestamp_ms: int = 0
    reply_to_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    mentions: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
