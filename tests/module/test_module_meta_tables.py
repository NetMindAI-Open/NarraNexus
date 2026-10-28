"""
@file_name: test_module_meta_tables.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 5b — the platform's former constant tables about modules (display, decision metadata, default / base / always-load sets, instance prefixes, roles) are read from each module's own ModuleConfig, so a plugin module is described like a builtin.
"""
from __future__ import annotations

import pytest

from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system import module_registry, instance_prefix_for, is_task_module, module_by_role, module_config
from narranexus.platform.module_system._module_impl.loader import ModuleLoader
from narranexus.platform.module_system._module_impl.metadata import get_all_modules_metadata, get_module_metadata, get_task_modules
from narranexus.platform.module_system._module_impl.selector import ModuleSelector
from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.module_system.slots import MODULES_SLOT
from narranexus.platform.schema.module_schema import ModuleConfig, ModuleDecisionMeta, ModuleDisplay


class AcmeNotesModule(XYZBaseModule):
    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="AcmeNotesModule", priority=42, enabled=True, description="Keeps the user's notes",
            default=True, instance_prefix="notes",
            display=ModuleDisplay(icon="📝", name="Notes", desc="Personal notes"),
            decision=ModuleDecisionMeta(capabilities=["Store a note"], use_cases=["Remember something"], instance_type="persistent"),
        )

    async def contribute_instructions(self, ctx_data):
        return ""

    async def contribute_turn_context(self, ctx_data):
        return ""


@pytest.fixture
def plugin_module():
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    dispose = KERNEL_REGISTRIES.registry_for(MODULES_SLOT).register_contribution(
        Contribution("AcmeNotesModule", lambda: AcmeNotesModule, meta={"plugin_id": "acme.notes", "channel": False}), owner="acme.notes"
    )
    yield
    dispose.dispose()


def test_builtin_declarations_reproduce_the_former_tables():
    assert ModuleSelector().get_base_modules() == ["AwarenessModule", "BasicInfoModule", "ChatModule"]
    assert ModuleLoader.default_modules(module_registry) == ["ChatModule", "BasicInfoModule", "AwarenessModule", "SocialNetworkModule", "JobModule", "MessageBusModule"]
    always = ModuleLoader.always_load_modules(module_registry)
    assert {"SkillModule", "CommonToolsModule", "GeneralMemoryModule", "NexusPluginsModule"} <= set(always) and "LarkModule" in always
    assert instance_prefix_for("ChatModule") == "chat" and instance_prefix_for("AwarenessModule") == "aware" and instance_prefix_for("LarkModule") == "lark"
    assert is_task_module("JobModule") and not is_task_module("ChatModule") and not is_task_module("Nope")
    assert module_by_role("chat") == "ChatModule" and module_by_role("jobs") == "JobModule" and module_by_role("nothing") is None
    assert get_task_modules() == ["JobModule"] and get_module_metadata("JobModule")["typical_instance_id"] == "job_{uuid8}"
    assert module_config("JobModule").always_available_tools and module_config("ChatModule").display.icon == "💬"


def test_a_plugin_module_is_described_like_a_builtin(plugin_module):
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_display import module_display

    assert "AcmeNotesModule" in ModuleLoader.default_modules(module_registry)
    assert instance_prefix_for("AcmeNotesModule") == "notes" and instance_prefix_for("UnknownThingModule") == "unknownthing"
    assert module_display("AcmeNotesModule") == {"icon": "📝", "name": "Notes", "desc": "Personal notes"}
    assert module_display("NoSuchModule") == {"icon": "🔌", "name": "NoSuch", "desc": ""}
    text = get_all_modules_metadata()
    assert "## AcmeNotesModule" in text and "Store a note" in text and "`notes_{uuid8}`" in text
    assert text.index("## ChatModule") < text.index("## AcmeNotesModule")  # priority order


@pytest.mark.asyncio
async def test_role_instances_and_task_hooks_come_from_declarations(db_client, plugin_module):
    from narranexus.platform.module_system import InstanceFactory
    from narranexus.platform.module_system._module_impl.instance_decision import module_overview_text

    factory = InstanceFactory(db_client)
    inst = await factory.ensure_role_instance("agent_r", "social_network")
    assert inst is not None and inst.module_class == "SocialNetworkModule" and inst.instance_id.startswith("social_")
    assert (await factory.ensure_role_instance("agent_r", "social_network")).instance_id == inst.instance_id  # idempotent
    assert await factory.ensure_role_instance("agent_r", "no_such_role") is None
    overview = module_overview_text()
    assert "## Task Modules" in overview and "- **JobModule**" in overview and "- **AcmeNotesModule**: Keeps the user's notes" in overview
    assert overview.index("- **ChatModule**") < overview.index("- **AcmeNotesModule**")
    # the base class hook is a no-op; JobModule reschedules (exercised through the instance handler tests)
    await XYZBaseModule.on_instance_activated("x", db_client)
