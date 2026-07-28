"""
@file_name: netmind_free.py
@author: Bin Liang
@date: 2026-07-28
@description: Driver for the platform free-tier card.

The free tier is a NetMind card whose key is a wallet on OUR gateway instead of
the user's own NetMind account — same two-row shape, same protocols, same
auth types, same aggregator limitations. So this driver is a subclass with a
different registry key and nothing else: the moment it needed its own config
building, the claim "the free tier is an ordinary NetMind card" would have
stopped being true.

Subclassing (rather than registering NetMindDriver twice) keeps the
relationship explicit and makes any future NetMind-specific behaviour apply
here automatically.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.providers.driver.drivers.netmind import (
    NetMindDriver,
)
from xyz_agent_context.agent_framework.providers.driver.registry import register


@register
class NetMindFreeDriver(NetMindDriver):
    """Platform-funded free-tier card, served through the LiteLLM gateway."""

    @classmethod
    def driver_type(cls) -> str:
        return "netmind_free"
