"""
@file_name: test_spec.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for the plugin install contract dataclasses.
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.integrations.plugins.spec import InstallComponent, PluginSpec


def test_install_component_is_frozen():
    component = InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43")
    assert component.kind == "pip"
    assert component.requirement == "claude-agent-sdk==0.1.43"
    with pytest.raises(dataclasses.FrozenInstanceError):
        component.requirement = "other==1.0"  # type: ignore[misc]


def test_plugin_spec_is_frozen_and_holds_components_tuple():
    spec = PluginSpec(
        id="claude_code",
        display_name="Claude Code",
        framework_name="claude_code",
        components=(InstallComponent(kind="pip", requirement="claude-agent-sdk==0.1.43"),),
        probe_package="claude_agent_sdk",
        user_version_source="npm_cli",
        size_hint="~190 MB",
    )
    assert spec.id == "claude_code"
    assert isinstance(spec.components, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.display_name = "Renamed"  # type: ignore[misc]
