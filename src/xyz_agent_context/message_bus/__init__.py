"""
@file_name: __init__.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBus package for inter-agent communication

Provides pluggable message bus implementations for agent-to-agent messaging,
channel management, agent discovery, and delivery tracking.
"""

from .cloud_bus import CloudMessageBus
from .local_bus import LocalMessageBus
from .message_bus_service import MessageBusService
from .message_bus_trigger import MessageBusTrigger
from .schemas import BusAgentInfo, BusChannel, BusChannelMember, BusMessage

# Register the MessageBus channel handler so chat_module can recognise
# bus-triggered reply tools and render bus rows with a distinct prefix.
# MessageBus reuses send_message_to_user_directly (the trigger prompt
# explicitly tells agents to use it for Owner Relay), so the default
# extractor is sufficient — we only need to override the prefix.
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)

try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="message_bus",
        user_reply_tool_names=("send_message_to_user_directly",),
        row_prefix_template="[Bus · from agent={from_agent}]",
    ))
except ValueError:
    pass


__all__ = [
    "MessageBusService",
    "LocalMessageBus",
    "CloudMessageBus",
    "MessageBusTrigger",
    "BusMessage",
    "BusChannel",
    "BusChannelMember",
    "BusAgentInfo",
]
