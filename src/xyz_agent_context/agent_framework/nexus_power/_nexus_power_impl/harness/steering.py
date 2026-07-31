"""
@file_name: steering.py
@author: Bin Liang
@date: 2026-07-29
@description: Step-boundary steering inlets. The DRAIN_STEERING call
site exists from day one; v1 mounts the null inlet (locked empty by a
contract test); P4 swaps in the TriggerInbox implementation — the loop
never changes. Injection is append-only by contract (a prefix mutation
would void the prompt cache).
"""

from __future__ import annotations

from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderMessage


class NullSteeringInlet:
    """No steering source; always empty."""

    async def drain(self) -> list[ProviderMessage]:
        return []
