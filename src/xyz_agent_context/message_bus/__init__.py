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
# The reply list carries every tool that DELIVERS on a bus turn: the bus
# sends (answer-the-peer / group replies) and send_message_to_user_directly
# (Owner Relay). Its live consumer is ChatModule._delivered_to_origin —
# the [DELIVERED-BG]/[NO-REPLY-BG] persistence split whose counts are the
# no-reply metric behind the delivery-fallback decision. Listing only the
# owner-chat tool recorded genuine bus deliveries as NO-REPLY (2026-08-01)
# and poisoned exactly that metric.
from xyz_agent_context.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
)

try:
    MessageSourceRegistry.register(MessageSourceHandler(
        name="message_bus",
        user_reply_tool_names=(
            "send_message_to_user_directly",
            "bus_send_message",
            "bus_send_to_agent",
        ),
        # Bus sends deliver to peer AGENTS — nothing appears in the
        # owner's web chat. Only the owner-notify tool is owner-visible,
        # so session anchoring and chat-history persistence ignore
        # agent-to-agent traffic (see MessageSourceHandler docstring).
        owner_visible_reply_tool_names=("send_message_to_user_directly",),
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
