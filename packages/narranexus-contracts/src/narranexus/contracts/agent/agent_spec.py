"""
@file_name: agent_spec.py
@author: Bin Liang
@date: 2026-09-03
@description: ``AgentSpec`` — an agent as one value: identity, persona, capability set, pipeline profile, model.

Team templates, bundle export/import, the agent settings page and the
plugin factory's "add a capability to this agent" all operate on this one
object instead of on scattered rows and flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from narranexus.contracts.agent.stages import Budgets


@dataclass(frozen=True)
class Persona:
    awareness: str = ""
    reply_language: str | None = None
    display_name: str = ""
    avatar: str | None = None


@dataclass(frozen=True)
class CapabilitySet:
    """Enabled capabilities by name, with per-agent overrides on top of the defaults."""

    enabled: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()  # explicit opt-outs of always_load capabilities
    settings: Mapping[str, Mapping[str, object]] = field(default_factory=dict)  # capability -> settings

    def is_enabled(self, name: str, *, default: bool) -> bool:
        if name in self.disabled:
            return False
        if name in self.enabled:
            return True
        return default


@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
    framework: str


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    owner_id: str
    persona: Persona
    capabilities: CapabilitySet
    pipeline_profile: str  # PipelineProfile id
    model: ModelIdentity
    budgets: Budgets = field(default_factory=Budgets)


__all__ = ["AgentSpec", "CapabilitySet", "ModelIdentity", "Persona"]
