"""
@file_name: channel_sender_registry.py
@author: Bin Liang
@date: 2026-03-10
@description: Channel sender registry

Each IM channel module registers its sender capability at init time.
Composite operations like contact_agent route through this registry.

Usage:
    # LarkModule registers at init
    ChannelSenderRegistry.register("lark", lark_send_to_agent)

    # Composite operations use it
    sender = ChannelSenderRegistry.get_sender("lark")
    if sender:
        await sender(agent_id, chat_id, message)
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger


# Sender function signature: async def sender(agent_id, target_id, message, **kwargs) -> dict
SenderFunction = Callable[..., Coroutine[Any, Any, Dict[str, Any]]]


class ChannelSenderRegistry:
    """
    Channel sender registry.

    Class-level registry where each channel module registers its send function.
    Composite operations like contact_agent select channels via this registry.
    """
    _senders: Dict[str, SenderFunction] = {}

    @classmethod
    def register(cls, channel: str, sender_fn: SenderFunction) -> None:
        """
        Register a channel sender.

        Args:
            channel: Channel name, e.g. "lark", "slack"
            sender_fn: Async sender function
        """
        cls._senders[channel] = sender_fn
        logger.info(f"ChannelSenderRegistry: registered sender for '{channel}'")

    @classmethod
    def unregister(cls, channel: str) -> None:
        """
        Unregister a channel sender.

        Args:
            channel: Channel name
        """
        cls._senders.pop(channel, None)
        logger.info(f"ChannelSenderRegistry: unregistered sender for '{channel}'")

    @classmethod
    def get_sender(cls, channel: str) -> Optional[SenderFunction]:
        """
        Get the sender for a specific channel.

        Args:
            channel: Channel name

        Returns:
            Sender function, or None if not registered
        """
        return cls._senders.get(channel)

    @classmethod
    def available_channels(cls) -> List[str]:
        """
        Get all registered channel names.

        Returns:
            List of channel names
        """
        return list(cls._senders.keys())

    @classmethod
    def has_channel(cls, channel: str) -> bool:
        """
        Check if a channel is registered.

        Args:
            channel: Channel name

        Returns:
            True if registered
        """
        return channel in cls._senders
