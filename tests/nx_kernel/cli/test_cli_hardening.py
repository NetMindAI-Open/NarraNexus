"""
@file_name: test_cli_hardening.py
@author: Bin Liang
@date: 2026-09-07
@description: The scaffold templates resolve from an installed wheel (packaged copy first, source checkout second, plain error otherwise) and the wheel actually carries them; `narranexus plugin doctor` exits non-zero when anything is rejected; `plugin enable` cannot bypass the permissions gate without --ack.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from narranexus.cli import scaffold
from narranexus.cli.main import main as cli
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

REPO = Path(__file__).resolve().parents[3]


def _plugin(home: Path, pid: str, *, min_app: str = "0.0.0", permissions: dict | None = None) -> Path:
    root = home / pid
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    manifest = {"id": pid, "version": "1.0.0", "displayName": pid, "minAppVersion": min_app, "hosts": ["backend"], "backend": {"activate": True}, "activationEvents": ["onStartup"]}
    if permissions:
        manifest["permissions"] = permissions
    (root / "narranexus-plugin.json").write_text(json.dumps(manifest))
    return root


def test_templates_resolve_and_ship_in_the_wheel():
    d = scaffold.resolve_templates_dir()
    assert (d / "routes" / "manifest.fragment.json").is_file()
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
    include = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert include["templates"] == "narranexus/cli/resources/templates"


def test_doctor_exits_non_zero_when_a_plugin_is_rejected(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    root = _plugin(home, "acme.old", min_app="99.0.0")
    RegistryStore(path=home / "registry.json", lkg=home / "registry.lkg.json").register("acme.old", PluginRecord(path=str(root), installed_version="1.0.0"))
    assert cli(["plugin", "doctor", "--json"]) == 1
    assert "acme.old" in json.loads(capsys.readouterr().out)["rejected"]


def test_enable_refuses_a_gated_plugin_without_ack(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    root = _plugin(home, "acme.needy", permissions={"subprocess": True})
    assert cli(["plugin", "install", str(root)]) == 0
    store = RegistryStore(path=home / "registry.json", lkg=home / "registry.lkg.json")
    assert store.read().plugins["acme.needy"].enabled is False
    assert cli(["plugin", "enable", "acme.needy"]) == 2
    assert store.read().plugins["acme.needy"].enabled is False
    assert cli(["plugin", "enable", "acme.needy", "--ack"]) == 0
    rec = store.read().plugins["acme.needy"]
    assert rec.enabled is True and rec.permissions_acknowledged and rec.acknowledged_permissions == ["subprocess"]
