"""
@file_name: test_paths.py
@author: Bin Liang
@date: 2026-09-03
@description: The plugin tree layout is env-overridable, and the legacy plugin_paths agrees with the kernel on the home.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.kernel.plugins import paths


def test_home_override_and_derived_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(paths.ENV_PLUGIN_HOME, str(tmp_path / "home"))
    home = paths.plugin_home()
    assert home == tmp_path / "home"
    assert paths.plugin_dir("acme.weather") == home / "acme.weather"
    assert paths.deps_dir("acme.weather", mode="copy") == home / "acme.weather" / "pyenv"
    assert paths.deps_dir("acme.weather", mode="link") == tmp_path / "plugin-deps" / "acme.weather"
    assert paths.registry_path() == home / "registry.json"
    assert paths.lkg_path() == home / "registry.lkg.json"
    assert paths.boot_marker_path("backend") == home / ".booting-backend"
    assert paths.manifest_path(home / "x") == home / "x" / "narranexus-plugin.json"
    assert paths.ensure_home().is_dir()


def test_default_home_is_under_the_user_profile(monkeypatch):
    monkeypatch.delenv(paths.ENV_PLUGIN_HOME, raising=False)
    assert paths.plugin_home() == Path.home() / ".narranexus" / "plugins"


def test_legacy_plugin_paths_delegates_to_the_kernel(monkeypatch, tmp_path: Path):
    from narranexus.platform.agent_framework import plugin_paths

    monkeypatch.setenv(paths.ENV_PLUGIN_HOME, str(tmp_path))
    assert plugin_paths.plugin_home() == tmp_path
    assert plugin_paths.pyenv_dir() == tmp_path / "pyenv"
    assert plugin_paths.ENV_PLUGIN_HOME == paths.ENV_PLUGIN_HOME
