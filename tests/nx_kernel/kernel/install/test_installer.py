"""
@file_name: test_installer.py
@author: Bin Liang
@date: 2026-09-03
@description: The install pipeline: local link, GitHub release copy with deps, blocked versions, duplicates, upgrade and uninstall — registry written last.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from narranexus.kernel.plugins.install import InstallError, Installer
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import plugin_dir

from .conftest import MANIFEST, write_local_plugin


def _installer(plugin_home: Path, client=None, **kw) -> Installer:
    runner = kw.pop("runner", lambda cmd, timeout: subprocess.CompletedProcess(cmd, 0, "", ""))
    kw.setdefault("host", "1.19.0")
    return Installer(store=RegistryStore(path=plugin_home / "registry.json", lkg=plugin_home / "lkg.json"), client=client, runner=runner, **kw)


def test_local_link_install_registers_in_place(plugin_home: Path, tmp_path: Path):
    src = write_local_plugin(tmp_path / "dev" / "acme.weather")
    inst = _installer(plugin_home)
    result = inst.install(str(src), installed_by="agent:a7", scope="agent:a7")
    assert result.mode == "link" and result.path == src.resolve() and result.permissions["network"] == ["api.weather.com"]
    rec = inst.store.read().plugins["acme.weather"]
    assert rec.state == "registered" and rec.scope == "agent:a7" and rec.installed_by == "agent:a7" and rec.mode == "link"
    assert rec.source.type == "local" and "narranexus-plugin.json" in rec.source.assets_sha256
    with pytest.raises(InstallError, match="already installed"):
        inst.install(str(src))
    assert not [p for p in plugin_home.iterdir() if p.name.startswith("nx-plugin-install-")]  # staging cleaned


def test_release_install_copies_installs_deps_and_records_hashes(plugin_home: Path, fake_github):
    commands = []

    def runner(cmd, timeout):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    inst = _installer(plugin_home, fake_github, runner=runner)
    result = inst.install("acme/narranexus-weather@1.2.0", permissions_acknowledged=True)
    assert result.path == plugin_dir("acme.weather") and result.mode == "copy" and result.deps_installed == ("httpx>=0.27",)
    assert (result.path / "backend" / "__init__.py").is_file() and (result.path / "frontend" / "dist" / "plugin.js").is_file()
    target = commands[0][commands[0].index("--target") + 1]
    assert target.endswith("/pyenv") and target.startswith(str(plugin_home))  # into the staged tree's own pyenv, moved into place
    rec = inst.store.read().plugins["acme.weather"]
    assert rec.source.type == "github" and rec.source.tag == "1.2.0" and set(rec.source.assets_sha256) >= {"backend.zip", "plugin.js"}
    assert rec.permissions_acknowledged is True


def test_blocked_and_incompatible_are_refused_before_any_write(plugin_home: Path, fake_github):
    inst = _installer(plugin_home, fake_github, blocked={"acme.weather": {"below": "2.0.0", "reason": "steals keys"}})
    with pytest.raises(InstallError, match="steals keys"):
        inst.install("acme/narranexus-weather@1.2.0")
    assert not inst.store.path.exists()
    old_host = _installer(plugin_home, fake_github, host="0.0.1")
    src = write_local_plugin(plugin_home.parent / "dev2", {**MANIFEST, "minAppVersion": "1.0.0"})
    (src / "versions.json").write_text(json.dumps({"1.0.0": "0.0.1", "1.2.0": "1.0.0"}))
    with pytest.raises(InstallError, match="versions.json suggests 1.0.0"):
        old_host.install(str(src))


def test_failed_deps_do_not_register(plugin_home: Path, fake_github):
    inst = _installer(plugin_home, fake_github, runner=lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, "", "boom"))
    with pytest.raises(Exception, match="boom"):
        inst.install("acme/narranexus-weather@1.2.0")
    assert "acme.weather" not in inst.store.read().plugins
    assert not plugin_dir("acme.weather").exists()


def test_upgrade_and_uninstall(plugin_home: Path, fake_github):
    inst = _installer(plugin_home, fake_github)
    inst.install("acme/narranexus-weather@1.2.0")
    check = inst.check_update("acme.weather")
    assert check.installed == "1.2.0" and check.available is None  # latest is the same tag
    result = inst.upgrade("acme.weather")
    assert result.version == "1.2.0" and inst.store.read().plugins["acme.weather"].installed_version == "1.2.0"
    inst.uninstall("acme.weather")
    assert "acme.weather" not in inst.store.read().plugins and not plugin_dir("acme.weather").exists()
    with pytest.raises(InstallError, match="not installed"):
        inst.uninstall("acme.weather")
