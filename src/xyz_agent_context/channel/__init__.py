"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-03-10
@description: IM channel protocol layer

This package defines shared protocols for IM channel modules (Slack, Lark, etc.):
- ChannelContextBuilderBase: Abstract base for prompt construction
- channel_contact_utils: Read/write utils for contact_info.channels
- channel_prompts: Shared prompt templates

Inter-agent communication is handled by MessageBusModule (see
module/message_bus_module/); this package only covers IM-to-user
channels. The old Matrix-era ChannelSenderRegistry has been removed
along with its sole consumer, the `contact_agent` MCP tool.
"""

from .channel_context_builder_base import ChannelContextBuilderBase, ChannelHistoryConfig
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
    "get_channel_info",
    "set_channel_info",
    "get_preferred_channel",
    "get_room_id",
    "set_room_id",
    "merge_contact_info",
    "normalize_contact_info",
]
