"""
@file_name: test_capability_service.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 5c — per-agent capability enablement: builtin modules default on, plugin modules default off (context budget), base modules cannot be disabled, explicit rows win, the loader binds only enabled modules, and the budget warns past 2× the builtin baseline.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.module_system.capability_service import CapabilityService
from narranexus.platform.module_system.slots import MODULES_SLOT
from narranexus.platform.schema.module_schema import ModuleConfig


class AcmeHeavyModule(XYZBaseModule):
    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(name="AcmeHeavyModule", priority=60, enabled=True, description="Heavy plugin module", always_load=True, context_cost_hint=5000)

    async def contribute_instructions(self, ctx_data):
        return "ACME HEAVY INSTRUCTIONS"

    async def contribute_turn_context(self, ctx_data):
        return ""

    async def mcp_server(self):
        return None


@pytest.fixture
def plugin_module():
    dispose = KERNEL_REGISTRIES.registry_for(MODULES_SLOT).register_contribution(
        Contribution("AcmeHeavyModule", lambda: AcmeHeavyModule, meta={"plugin_id": "acme.heavy", "channel": False}), owner="acme.heavy"
    )
    yield
    dispose.dispose()


class AcmeBuiltinBaselineModule(XYZBaseModule):
    """Registered under a ``builtin.`` owner so `default_enabled` counts it
    toward the baseline — no shipped builtin currently declares a nonzero
    ``context_cost_hint``, so a budget-overrun scenario needs a fixture that
    supplies one, or `over_budget` can never observably become True."""

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(name="AcmeBuiltinBaselineModule", priority=1, enabled=True, description="Fake builtin baseline module", always_load=True, context_cost_hint=1000)

    async def contribute_instructions(self, ctx_data):
        return ""

    async def contribute_turn_context(self, ctx_data):
        return ""

    async def mcp_server(self):
        return None


@pytest.fixture
def builtin_baseline_module():
    dispose = KERNEL_REGISTRIES.registry_for(MODULES_SLOT).register_contribution(
        Contribution("AcmeBuiltinBaselineModule", lambda: AcmeBuiltinBaselineModule, meta={"plugin_id": "builtin.acme_baseline", "channel": False}),
        owner="builtin.acme_baseline",
    )
    yield
    dispose.dispose()


@pytest.mark.asyncio
async def test_defaults_locks_and_explicit_rows(db_client, plugin_module):
    svc = CapabilityService(db_client)
    enabled = await svc.enabled_map("agent_c")
    assert enabled["ChatModule"] and enabled["JobModule"] and enabled["AcmeHeavyModule"] is False
    assert svc.is_locked("ChatModule") and svc.is_locked("AwarenessModule") and not svc.is_locked("JobModule")
    with pytest.raises(ValueError, match="base module"):
        await svc.set_enabled("agent_c", "ChatModule", False)
    with pytest.raises(ValueError, match="unknown module"):
        await svc.set_enabled("agent_c", "NopeModule", True)
    await svc.set_enabled("agent_c", "AcmeHeavyModule", True)
    await svc.set_enabled("agent_c", "JobModule", False)
    enabled = await svc.enabled_map("agent_c")
    assert enabled["AcmeHeavyModule"] is True and enabled["JobModule"] is False
    assert await svc.is_enabled("agent_c", "AcmeHeavyModule") and not await svc.is_enabled("agent_other", "AcmeHeavyModule")
    assert await svc.reset("agent_c", "JobModule") and (await svc.enabled_map("agent_c"))["JobModule"] is True
    view = await svc.overview("agent_c")
    heavy = next(c for c in view["capabilities"] if c["module_class"] == "AcmeHeavyModule")
    assert heavy == {**heavy, "enabled": True, "default_enabled": False, "explicit": True, "locked": False, "builtin": False, "owner": "acme.heavy", "context_cost_hint": 5000}
    assert view["budget"]["enabled_tokens"] >= 5000 and view["capabilities"][0]["priority"] <= view["capabilities"][-1]["priority"]


@pytest.mark.asyncio
async def test_budget_warns_past_twice_the_builtin_baseline(db_client, plugin_module, builtin_baseline_module):
    svc = CapabilityService(db_client)
    base = svc.budget(await svc.enabled_map("agent_b"))
    # Concrete numbers, not just "under budget": the baseline is exactly the
    # 1000-token fixture builtin (default-enabled), and nothing else is on
    # yet, so the ratio is exactly 1.0 — not merely "not over budget".
    assert base == {"baseline_tokens": 1000, "enabled_tokens": 1000, "ratio": 1.0, "over_budget": False}

    await svc.set_enabled("agent_b", "AcmeHeavyModule", True)
    enabled = await svc.enabled_map("agent_b")

    from loguru import logger

    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m.record["message"]), level="TRACE")
    try:
        over = svc.warn_if_over_budget("agent_b", enabled)
    finally:
        logger.remove(handler_id)

    # Enabling the 5000-token plugin module on top of the 1000-token baseline
    # is 6x the baseline — past the 2x warn threshold. Assert the concrete
    # numbers this scenario must produce, not a value re-derived from the
    # same call under test (that would make the assertion tautological).
    assert over == {"baseline_tokens": 1000, "enabled_tokens": 6000, "ratio": 6.0, "over_budget": True}
    assert any("6000" in m and "1000" in m and "6.0" in m for m in messages), messages


@pytest.mark.asyncio
async def test_loader_drops_disabled_capabilities_before_binding(db_client, plugin_module):
    """`_drop_disabled` is the one chokepoint every load path (decision, fast
    path) runs before `_create_module_objects`: a disabled module's instance
    never binds, so its instructions, tools and hooks stay out of the turn."""
    from narranexus.platform.module_system import module_registry
    from narranexus.platform.module_system._module_impl.loader import ModuleLoader
    from narranexus.platform.schema.module_schema import InstanceStatus, ModuleInstance

    await db_client.insert("agents", {"agent_id": "agent_l", "agent_name": "L", "created_by": "u1"})
    loader = ModuleLoader(agent_id="agent_l", user_id="u1", database_client=db_client, module_map=dict(module_registry))
    insts = [ModuleInstance(instance_id=f"{n.lower()}_1", module_class=n, description="", status=InstanceStatus.ACTIVE, agent_id="agent_l", dependencies=[]) for n in ("ChatModule", "JobModule", "AcmeHeavyModule")]
    kept = {i.module_class for i in await loader._drop_disabled(insts)}
    assert kept == {"ChatModule", "JobModule"}  # the plugin module is off by default
    await CapabilityService(db_client).set_enabled("agent_l", "AcmeHeavyModule", True)
    await CapabilityService(db_client).set_enabled("agent_l", "JobModule", False)
    kept = {i.module_class for i in await loader._drop_disabled(insts)}
    assert kept == {"ChatModule", "AcmeHeavyModule"}
    # the always-load pass respects the switches too (traditional list)
    assert "AcmeHeavyModule" in ModuleLoader.always_load_modules(loader.module_map)
    bound = loader._create_module_objects(await loader._drop_disabled(insts))
    assert {i.module_class for i in bound if i.module is not None} == {"ChatModule", "AcmeHeavyModule"}
    # no database client → nothing filtered (unit-test shape)
    assert len(await ModuleLoader("a", "u", None, dict(module_registry))._drop_disabled(insts)) == 3
