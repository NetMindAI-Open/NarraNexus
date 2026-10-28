"""
@file_name: test_module_is_a_capability.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 5c — an XYZBaseModule IS a Capability: its lifecycle methods carry the stage names, `meta` derives from its ModuleConfig, `participations()` lists the module tier's stages, and `contribute_tools` composes the three tool hooks fail-open (a stale override signature is logged loudly). The LegacyModuleAdapter is gone.
"""
from __future__ import annotations

import importlib

import pytest

from narranexus.contracts.agent.capability import STAGE_METHODS, TIER_STAGES, Capability, CapabilityTier, ToolSurface
from narranexus.contracts.agent.stages import Stage
from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.schema.module_schema import MCPServerConfig, ModuleConfig


class _Mod(XYZBaseModule):
    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(name="_Mod", priority=7, enabled=True, description="probe", instance_prefix="probe", context_cost_hint=120)

    async def contribute_instructions(self, ctx_data):
        return "hi"

    async def contribute_turn_context(self, ctx_data):
        return ""

    async def mcp_server(self):
        return MCPServerConfig(server_name="probe_module", server_url="http://mcp:7801/mcp/probe_module/sse")

    async def expressive_tools(self, ctx_data=None):
        return ["mcp__probe_module__say"]

    async def disallowed_tools(self, ctx_data=None):
        return ["mcp__probe_module__hidden"]


def test_module_satisfies_the_capability_contract():
    m = _Mod("a1", "u1", None)
    assert isinstance(m, Capability)
    assert m.meta.name == "_Mod" and m.meta.tier is CapabilityTier.MODULE and m.meta.priority == 7
    assert m.meta.instance_prefix == "probe" and m.meta.context_cost_hint == 120 and m.meta.always_load
    # per-agent state is not part of the static description
    assert dict(m.meta.requires) == {} and m.config.enabled is True
    stages = m.participations()
    assert set(stages) == set(TIER_STAGES[CapabilityTier.MODULE]) - {Stage.RECALL, Stage.ACT}
    for stage, participant in stages.items():
        assert participant is m and any(hasattr(participant, name) for name in STAGE_METHODS[stage])
    # the nine lifecycle cells carry their stage names; the old names are gone
    for old in ("hook_data_gathering", "get_instructions", "get_turn_context", "get_mcp_config", "get_expressive_tools", "get_disallowed_tools", "hook_persist_turn", "hook_after_event_execution", "owns_working_source"):
        assert not hasattr(XYZBaseModule, old), old


@pytest.mark.asyncio
async def test_contribute_tools_composes_the_three_hooks():
    surface = await _Mod("a1", "u1", None).contribute_tools(None)
    assert isinstance(surface, ToolSurface)
    assert surface.mcp_servers["probe_module"]["server_url"].endswith("/mcp/probe_module/sse")
    assert surface.expressive_tools == ("mcp__probe_module__say",) and surface.disallowed_tools == ("mcp__probe_module__hidden",)


@pytest.mark.asyncio
async def test_contribute_tools_fails_open_and_flags_a_stale_signature(caplog):
    class _Stale(_Mod):
        async def expressive_tools(self):  # type: ignore[override] — the drift under test
            return ["x"]

        async def disallowed_tools(self, ctx_data=None):
            raise RuntimeError("boom")

    import logging

    from loguru import logger

    handler_id = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        with caplog.at_level(logging.WARNING):
            surface = await _Stale("a1", "u1", None).contribute_tools(object())
    finally:
        logger.remove(handler_id)
    assert surface.expressive_tools == () and surface.disallowed_tools == () and "probe_module" in surface.mcp_servers
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "expressive_tools signature mismatch" in joined and "disallowed_tools failed" in joined


def test_the_adapter_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("narranexus.platform.module_system.capability_adapter")
