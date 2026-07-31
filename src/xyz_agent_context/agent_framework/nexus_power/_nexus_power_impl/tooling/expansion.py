"""
@file_name: expansion.py
@author: Bin Liang
@date: 2026-07-29
@description: Dynamic capability expansion — the framework's generic
"expandable" mechanism.

Framework neutrality (Owner decision): the framework does not know what
a "module" is. It knows ``Expandable`` — a bundle of optional elements
(instructions, MCP servers, skill dirs, env). NarraNexus translates its
modules into Expandables and passes them in; any other platform passes
its own. Lifetime semantics: initial expansions run before the first
model call (their instructions join the prompt — cache-friendly);
mid-turn expansions return instructions through the tool result
(append-only by nature); everything expires with the turn — cross-turn
"memory" belongs to the platform, which consumes expansion events from
the log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from xyz_agent_context.agent_framework.nexus_power.contracts.model import McpServerSpec

AddServersFn = Callable[[dict[str, McpServerSpec]], Awaitable[None]]
AddEnvFn = Callable[[dict[str, str]], None]
AddExpressiveFn = Callable[[tuple[str, ...]], None]


@dataclass(frozen=True)
class Expandable:
    """One expandable capability bundle (every element optional)."""

    key: str
    card: str
    instructions: str = ""
    mcp_servers: dict[str, McpServerSpec] = field(default_factory=dict)
    skill_dirs: tuple[str, ...] = ()
    extra_env: dict[str, str] = field(default_factory=dict)
    # Delivery tools this capability contributes (fully-qualified names).
    # Expanding grants them to the expression contract, so a reply tool
    # that ARRIVES via expansion is recognized as a reply tool — without
    # this, a channel capability loaded mid-turn would deliver through a
    # tool the harness still calls non-expressive.
    expressive_tools: tuple[str, ...] = ()


class CapabilityExpander:
    """Executes expansions against injected seams (MCP attach, env merge,
    expression grant)."""

    def __init__(
        self,
        catalog: tuple[Expandable, ...],
        *,
        add_mcp_servers: AddServersFn,
        add_env: AddEnvFn,
        add_expressive: AddExpressiveFn | None = None,
    ) -> None:
        self._catalog = {e.key: e for e in catalog}
        self._add_mcp_servers = add_mcp_servers
        self._add_env = add_env
        self._add_expressive = add_expressive
        self._expanded: set[str] = set()

    def card_index(self) -> str:
        """The CARD index (discovery is never trimmed): one line per key."""
        return "\n".join(
            f"- {e.key}: {e.card}" for e in sorted(self._catalog.values(), key=lambda e: e.key)
        )

    def expanded_keys(self) -> frozenset[str]:
        return frozenset(self._expanded)

    async def expand(self, key: str) -> str:
        """Expand one capability (idempotent). Raises KeyError on unknown
        keys — the tool handler translates that into a normal error result."""
        expandable = self._catalog.get(key)
        if expandable is None:
            raise KeyError(key)
        if key in self._expanded:
            return expandable.instructions or f"(capability '{key}' was already active)"
        if expandable.mcp_servers:
            await self._add_mcp_servers(dict(expandable.mcp_servers))
        if expandable.extra_env:
            self._add_env(dict(expandable.extra_env))
        if expandable.expressive_tools and self._add_expressive is not None:
            self._add_expressive(expandable.expressive_tools)
        self._expanded.add(key)
        return expandable.instructions or f"(capability '{key}' is now active)"

    async def expand_initial(self, keys: frozenset[str]) -> str:
        """Start-of-turn batch expansion (same path, same idempotency).

        Runs before the first model call, so the returned instruction
        block may join the PROMPT (stable side) rather than the tail —
        to the model this is indistinguishable from born-resident.
        Unknown keys fail fast: a platform passing a bad initial set is
        a wiring bug, not a model mistake.
        """
        blocks = [await self.expand(key) for key in sorted(keys)]
        return "\n\n".join(b for b in blocks if b)
