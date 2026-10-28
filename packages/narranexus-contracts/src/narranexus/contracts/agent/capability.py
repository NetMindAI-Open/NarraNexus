"""
@file_name: capability.py
@author: Bin Liang
@date: 2026-09-03
@description: A capability is a set of stage participations plus metadata (the horizontal axis).

The five tiers are not five mechanisms — they are the same ``Capability``
contract with more or fewer cells of the capability × stage matrix filled
(``TIER_STAGES`` is the table):

    TOOL              Act only (``tools``; a plugin's ``ToolProvider.list_tools`` —
                      the MCP server behind a tool is declared through
                      ``agent.capabilities.mcp_servers``, not through a tool
                      participant's Assemble cell)
    CONTEXT_PROVIDER  Assemble only (instructions / turn context / data)
    SKILL             Assemble (its entry in the skills table) + Act (scripts)
    MEMORY_KIND       Recall (``recall``) + Commit + Reflect
    MODULE            every participant stage (all but Compose) + tools + tables + triggers

A ``StageParticipant`` is what a capability exposes for one stage. Every
method is optional (structural Protocol): a participant declares only the
cells it fills, and the runtime calls only what is declared. ``XYZBaseModule``
IS a capability (batch 5c): its lifecycle methods carry these very names
(``claims_source`` / ``gather`` / ``contribute_instructions`` /
``contribute_turn_context`` / ``contribute_tools`` — composed from the module's
``mcp_server`` / ``expressive_tools`` / ``disallowed_tools`` — / ``persist_turn``
/ ``after_turn``) and ``participations()`` returns the module itself for each
stage it fills; there is no adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Mapping, Protocol, Sequence, runtime_checkable

from narranexus.contracts.agent.stages import Stage


class CapabilityTier(str, Enum):
    TOOL = "tool"
    CONTEXT_PROVIDER = "context_provider"
    SKILL = "skill"
    MEMORY_KIND = "memory_kind"
    MODULE = "module"


@dataclass(frozen=True)
class CapabilityMeta:
    """Static description; the cells formerly scattered over seven constant tables."""

    name: str
    tier: CapabilityTier
    display_name: str = ""
    description: str = ""
    priority: int = 100  # lower sorts first in prompt assembly (Awareness=0, Chat=1)
    always_load: bool = False
    is_task_capability: bool = False  # created by decision, not auto-loaded
    provides_chat_history: bool = False
    context_cost_hint: int | None = None  # tokens, order of magnitude
    instance_prefix: str = ""
    requires: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSurface:
    """What a capability adds to (or removes from) the turn's tool surface."""

    mcp_servers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    expressive_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()


class StageParticipant(Protocol):
    """One capability's behaviour in the stages it fills. Every method optional.

    Deliberately NOT ``runtime_checkable``: ``isinstance`` would demand every
    method, and the whole point is that a participant declares only the cells
    it fills. Check presence per stage with ``STAGE_METHODS`` instead.
    """

    # Ingress
    def claims_source(self, working_source: str) -> bool: ...

    # Recall
    async def recall(self, ctx_data: Any) -> Any: ...

    # Assemble
    async def gather(self, ctx_data: Any) -> Any: ...
    async def contribute_instructions(self, ctx_data: Any) -> str: ...
    async def contribute_turn_context(self, ctx_data: Any) -> str: ...
    async def contribute_tools(self, ctx_data: Any) -> ToolSurface: ...

    # Act — async like the other cells: a participant may have to ask its
    # backing service (an MCP server's config, say) before it can name its tools.
    async def tools(self) -> Sequence[Any]: ...

    # Commit / Reflect
    async def persist_turn(self, params: Any) -> None: ...
    async def after_turn(self, params: Any) -> Awaitable[None] | None: ...


@runtime_checkable
class Capability(Protocol):
    """A named participant in the turn pipeline."""

    meta: CapabilityMeta

    def participations(self) -> Mapping[Stage, StageParticipant]:
        """Which stages this capability fills, and with what."""
        ...


@runtime_checkable
class ContextProvider(Protocol):
    """An L2 capability that only speaks in Assemble (slot ``agent.capabilities.context_providers``).

    Implement either or both methods: ``contribute_instructions`` must be
    byte-stable across turns (it joins the cacheable prefix);
    ``contribute_turn_context`` may vary (it joins the dynamic tail). Return
    an empty string to say nothing this turn. ``context_cost_hint`` is the
    order of magnitude of tokens added, shown in the factory page.
    """

    name: str
    context_cost_hint: int

    async def contribute_instructions(self, ctx_data: Any) -> str: ...

    async def contribute_turn_context(self, ctx_data: Any) -> str: ...


# Which StageParticipant methods belong to which stage. The runtime and the
# legacy adapter both derive from this single table.
STAGE_METHODS: Mapping[Stage, tuple[str, ...]] = {
    Stage.INGRESS: ("claims_source",),
    Stage.RECALL: ("recall",),
    Stage.ASSEMBLE: ("gather", "contribute_instructions", "contribute_turn_context", "contribute_tools"),
    Stage.ACT: ("tools",),
    Stage.COMMIT: ("persist_turn",),
    Stage.REFLECT: ("after_turn",),
}

# The stages each tier may fill — a stage-level upper bound that documents the
# model; no validator enforces it yet (``participations()`` implementations
# derive from it, nothing checks a capability against it). Compose is the
# platform's own stage (no participant method).
TIER_STAGES: Mapping[CapabilityTier, frozenset[Stage]] = {
    CapabilityTier.TOOL: frozenset({Stage.ACT}),
    CapabilityTier.CONTEXT_PROVIDER: frozenset({Stage.ASSEMBLE}),
    CapabilityTier.SKILL: frozenset({Stage.ASSEMBLE, Stage.ACT}),
    CapabilityTier.MEMORY_KIND: frozenset({Stage.RECALL, Stage.COMMIT, Stage.REFLECT}),
    CapabilityTier.MODULE: frozenset(s for s in Stage if s is not Stage.COMPOSE),
}


__all__ = [
    "STAGE_METHODS",
    "TIER_STAGES",
    "Capability",
    "CapabilityMeta",
    "ContextProvider",
    "CapabilityTier",
    "StageParticipant",
    "ToolSurface",
]
