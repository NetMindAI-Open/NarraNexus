"""
@file_name: conftest.py
@author: Bin Liang
@date: 2026-09-03
@description: Isolated plugin home + agent workspace for the self-extension tests; local mode forced.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.kernel.plugins.importer import plugin_finder, uninstall_synthetic_package
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    workspace = tmp_path / "ws" / "u1" / "a1"
    workspace.mkdir(parents=True)
    yield {"home": home, "workspace": workspace, "store": RegistryStore(path=home / "registry.json", lkg=home / "registry.lkg.json")}
    for pid in ("me.weather", "me.other", "me.tab"):
        uninstall_synthetic_package(pid)
        plugin_finder().unregister_deps(pid)


@pytest.fixture
def svc(env):
    from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.service import SelfExtensionService

    return SelfExtensionService("a1", "u1", workspace=env["workspace"], store=env["store"])
