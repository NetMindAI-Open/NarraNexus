"""
@file_name: test_module_registry_view.py
@author: Bin Liang
@date: 2026-09-04
@description: module_registry is a live view of the agent.capabilities.modules registry; the module facade resolves classes lazily; derived tables follow it.
"""
from __future__ import annotations

import importlib

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.module_system import module_registry
from narranexus.platform.module_system.registry import ModuleRegistry
from narranexus.platform.module_system.slots import MODULES_SLOT
from narranexus.kernel.plugins.builtins import load_builtins


EXPECTED_MODULES = {
    "AwarenessModule", "BasicInfoModule", "ChatModule", "SocialNetworkModule", "JobModule", "SkillModule",
    "MessageBusModule", "CommonToolsModule", "GeneralMemoryModule", "HomeAssistantModule", "NexusPluginsModule",
    "BrowserModule",
    "LarkModule", "SlackModule", "TelegramModule", "WeChatModule", "NarramessengerModule", "DiscordModule",
}


def test_module_map_lists_every_builtin_module_and_meta():
    names = set(module_registry)
    assert EXPECTED_MODULES == names and "ChatModule" in module_registry
    assert module_registry["SkillModule"].get_config().always_load is True  # the module declares it (batch 5b)
    assert module_registry.meta("LarkModule")["channel"] is True and module_registry.owner_of("LarkModule") == "builtin.channels.lark"
    assert module_registry["ChatModule"].__name__ == "ChatModule"


def test_package_re_exports_no_module_class():
    """Batch 5d: the registry is the only way to name a module — the package
    neither imports a builtin nor re-exports its class."""
    mod = importlib.import_module("narranexus.platform.module_system")
    assert module_registry["ChatModule"].__name__ == "ChatModule"
    with pytest.raises(AttributeError):
        _ = mod.ChatModule
    with pytest.raises(AttributeError):
        _ = mod.NoSuchModule


def test_every_module_has_a_builtin_manifest_and_view_drops_removed_owner():
    ids = {m.id for m in builtin_manifests()}
    for name in EXPECTED_MODULES:
        assert module_registry.owner_of(name) in ids, name
    regs = Registries()
    load_builtins(regs, "backend")
    view = ModuleRegistry(regs)
    assert "DiscordModule" in view
    regs.remove_owner("builtin.channels.discord")
    assert "DiscordModule" not in view and "ChatModule" in view


def test_derived_tables_follow_the_view():
    from narranexus.platform.module_system._module_impl.loader import ModuleLoader
    from narranexus.platform.module_system.module_runner import all_mcp_modules
    from narranexus.platform.module_system.registry import core_module_names

    assert set(core_module_names()) == EXPECTED_MODULES - {"LarkModule", "SlackModule", "TelegramModule", "WeChatModule", "NarramessengerModule", "DiscordModule"}
    from narranexus.platform.module_system import module_registry

    assert {"SkillModule", "CommonToolsModule", "GeneralMemoryModule", "NexusPluginsModule", "LarkModule"} <= set(ModuleLoader.always_load_modules(module_registry))
    assert "LarkModule" in all_mcp_modules() and "ChatModule" in all_mcp_modules()
