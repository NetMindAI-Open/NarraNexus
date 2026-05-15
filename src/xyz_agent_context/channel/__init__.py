"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-03-10
@description: IM channel protocol layer

This package defines shared protocols for IM channel modules (Lark, Slack, etc.):

Pre-Phase-1 (existing):
- ChannelContextBuilderBase: Abstract base for prompt construction
- ChannelSenderRegistry: Channel sender registration table
- channel_contact_utils: Read/write utils for contact_info.channels
- channel_prompts: Shared prompt templates

Phase 1 (IM abstraction — trigger side):
- ChannelTriggerBase: Abstract base for IM channel triggers
- ChannelDedupStore: Three-layer dedup cascade
- ChannelDebounceMerger: Rapid-fire message merge
- ChannelInboxWriter: Generic Inbox writer
- channel_audit_events (re-exported as ``audit_events``): event-type constants

Phase 2 (IM abstraction — module / agent-loop side):
- ChannelModuleBase: Abstract base for IM channel Modules

Inter-agent communication is handled by MessageBusModule (see
module/message_bus_module/); this package only covers IM-to-user
channels.
"""

from .channel_context_builder_base import ChannelContextBuilderBase, ChannelHistoryConfig
from .channel_sender_registry import ChannelSenderRegistry
from .message_source_handler import MessageSourceHandler, MessageSourceRegistry
from .channel_contact_utils import (
    get_channel_info,
    set_channel_info,
    get_preferred_channel,
    get_room_id,
    set_room_id,
    merge_contact_info,
    normalize_contact_info,
)
from .channel_dedup_store import ChannelDedupStore
from .channel_debounce_merger import ChannelDebounceMerger
from .channel_inbox_writer import ChannelInboxWriter
from .channel_trigger_base import ChannelTriggerBase
from .channel_module_base import ChannelModuleBase
from . import channel_audit_events as audit_events

__all__ = [
    # Existing
    "ChannelContextBuilderBase",
    "ChannelHistoryConfig",
    "ChannelSenderRegistry",
    "MessageSourceHandler",
    "MessageSourceRegistry",
    "get_channel_info",
    "set_channel_info",
    "get_preferred_channel",
    "get_room_id",
    "set_room_id",
    "merge_contact_info",
    "normalize_contact_info",
    # Phase 1 abstraction (trigger side)
    "ChannelTriggerBase",
    "ChannelDedupStore",
    "ChannelDebounceMerger",
    "ChannelInboxWriter",
    "audit_events",
    # Phase 2 abstraction (module side)
    "ChannelModuleBase",
]
