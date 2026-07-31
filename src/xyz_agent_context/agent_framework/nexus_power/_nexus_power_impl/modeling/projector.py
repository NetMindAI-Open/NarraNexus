"""
@file_name: projector.py
@author: Bin Liang
@date: 2026-07-29
@description: ContextProjector implementations.

v1 = PassthroughProjector: the materialized base (platform-built
messages plus the framework's appended harness prompt) followed by this
turn's ledger-projected messages. Compaction is present from v1 but as
a separate concern: the ``CompactionPolicy`` appends replacement
entries, the LEDGER substitutes them in its projection, and this class
just concatenates — upgrading compaction never touches projection.
"""

from __future__ import annotations

from typing import Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.protocols import LedgerView


class PassthroughProjector:
    """Base messages + ledger turn messages + a live tail block.

    The tail provider re-renders per-step state (today: the agent's
    plan) and lands as the LAST message — append-only by construction,
    so a growing plan never disturbs the cached prefix.
    """

    def __init__(
        self,
        base_messages: list[ProviderMessage],
        tail_provider: Callable[[], str] | None = None,
    ) -> None:
        # The base is platform property; the projector never rewrites it
        # ("materialization vs self-projection" boundary, made explicit).
        self._base = list(base_messages)
        self._tail_provider = tail_provider

    def project(self, ledger: LedgerView, profile: ProviderProfile) -> list[ProviderMessage]:
        from typing import cast

        provider_messages = getattr(ledger, "provider_messages", None)
        turn_messages = (
            cast(list[ProviderMessage], provider_messages())
            if callable(provider_messages)
            else []
        )
        projected = [*self._base, *turn_messages]
        tail = self._tail_provider() if self._tail_provider else ""
        if tail:
            projected.append({"role": "system", "content": tail})
        return projected
