"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-03-10
@description: IM channel protocol layer

This package defines shared protocols for IM channel modules (Matrix, Slack, etc.):
- ChannelContextBuilderBase: Abstract base for prompt construction
- ChannelSenderRegistry: Channel sender registration table
- channel_contact_utils: Read/write utils for contact_info.channels
- channel_prompts: Shared prompt templates

Note the distinction:
- schema/channel_tag.py: Infrastructure, used by Chat, Job, Matrix, etc.
- channel/: IM-channel-specific protocols, only needed by Matrix, Slack, etc.
"""

from .channel_context_builder_base import ChannelContextBuilderBase, ChannelHistoryConfig
from .channel_sender_registry import ChannelSenderRegistry
from .channel_contact_utils import (
    get_channel_info,
    set_channel_info,
    get_preferred_channel,
    get_room_id,
    set_room_id,
    merge_contact_info,
    normalize_contact_info,
)

__all__ = [
    "ChannelContextBuilderBase",
    "ChannelHistoryConfig",
    "ChannelSenderRegistry",
    "get_channel_info",
    "set_channel_info",
    "get_preferred_channel",
    "get_room_id",
    "set_room_id",
    "merge_contact_info",
    "normalize_contact_info",
]
