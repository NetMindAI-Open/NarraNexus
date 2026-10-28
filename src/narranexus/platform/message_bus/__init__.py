"""
@file_name: __init__.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBus package for inter-agent communication

Provides pluggable message bus implementations for agent-to-agent messaging,
channel management, agent discovery, and delivery tracking.

Importing this package registers NOTHING. The bus's message source (which tool
calls count as delivering a bus turn, how a bus row is labelled) used to be a
module-level ``MessageSourceRegistry.register`` here — a registration that fired
for anything that merely imported ``agent_framework``, and was absent whenever
nothing had. It now lives with the plugin that owns the bus module:
``builtin.message_bus``'s ``contribution.py``, into ``ingress.message_sources``.
"""

from .cloud_bus import CloudMessageBus
from .local_bus import LocalMessageBus
from .message_bus_service import MessageBusService
from .message_bus_trigger import MessageBusTrigger
from .schemas import BusAgentInfo, BusChannel, BusChannelMember, BusMessage

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
