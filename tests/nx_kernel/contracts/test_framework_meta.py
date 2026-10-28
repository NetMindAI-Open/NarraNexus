"""
@file_name: test_framework_meta.py
@author: Bin Liang
@date: 2026-09-03
@description: Framework install metadata rides the framework contributions and the backend installer table derives from it.
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta


def test_builtin_frameworks_carry_framework_meta():
    from narranexus.platform.agent_framework.loop.driver import framework_registry
    metas = {e.name: e.meta["framework"] for e in framework_registry().entries() if "framework" in e.meta}
    assert set(metas) >= {"nexus_power", "claude_code", "codex_cli"}
    assert all(isinstance(m, FrameworkMeta) for m in metas.values())
    assert metas["nexus_power"].install is None
    assert isinstance(metas["claude_code"].install, FrameworkInstall)
    assert [c.kind for c in metas["claude_code"].install.components] == ["pip", "npm"]
    assert metas["codex_cli"].install.probe_package == "openai_codex"


def _claude_install_component():
    from narranexus.platform.agent_framework.loop.driver import framework_registry
    entry = next(e for e in framework_registry().entries() if e.name == "claude_code")
    return entry.meta["framework"].install.components[0]


def test_backend_installer_table_is_derived_from_the_registry():
    from backend.integrations.plugins.registry import build_plugin_specs
    from backend.integrations.plugins.service import PluginService

    specs = build_plugin_specs()
    assert set(specs) == {"claude_code", "codex_cli"}
    assert specs["claude_code"].components[1].requirement.startswith("@anthropic-ai/claude-code@")
    # The component objects are the contract's own (no backend copy).
    assert specs["claude_code"].components[0] is _claude_install_component()
    assert set(PluginService()._specs) == set(specs)
