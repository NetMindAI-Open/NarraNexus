"""
@file_name: test_plugins_boot_distribution.py
@author: Bin Liang
@date: 2026-09-04
@description: The backend host reads NARRANEXUS_DIST once, boots that distribution, and snapshots the resolved slot bindings (default < distribution < narranexus.toml < env) to <plugin home>/run/bindings.resolved.json; a conflict is loud.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.contracts import BindingConflict
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(h))
    return h


def test_distribution_is_read_once_from_the_environment(monkeypatch):
    from backend import plugins_boot

    monkeypatch.setattr(plugins_boot, "_DISTRIBUTION", {})
    monkeypatch.delenv("NARRANEXUS_DIST", raising=False)
    assert plugins_boot.distribution() is None
    monkeypatch.setattr(plugins_boot, "_DISTRIBUTION", {})
    monkeypatch.setenv("NARRANEXUS_DIST", str(REPO / "distributions" / "minimal"))
    res = plugins_boot.distribution()
    assert res is not None and res.spec.id == "narranexus.minimal"
    monkeypatch.setenv("NARRANEXUS_DIST", str(REPO / "distributions" / "cloud"))
    assert plugins_boot.distribution() is res  # cached: the process runs one distribution


def test_bindings_snapshot_layers_distribution_toml_and_env(home: Path, monkeypatch):
    from backend.plugins_boot import write_runtime_bindings
    from narranexus.kernel.plugins.distribution import load_distribution, resolve_distribution

    spec, base = load_distribution(REPO / "distributions" / "cloud")
    res = resolve_distribution(spec, base, host_version="1.15.0")
    monkeypatch.delenv("NX_BIND__turn__recall", raising=False)
    out = write_runtime_bindings(res)
    assert out == home / "run" / "bindings.resolved.json"
    snap = json.loads(out.read_text())
    assert snap["one"]["kernel.auth"] == {"provider": "builtin.auth.netmind", "layer": "DISTRIBUTION", "origin": "narranexus.cloud"}

    (home / "narranexus.toml").write_text('[bindings]\n"kernel.auth" = "builtin.auth.local"\n')
    with pytest.raises(BindingConflict, match="distribution-only"):
        write_runtime_bindings(res)
    (home / "narranexus.toml").unlink()
    out = write_runtime_bindings(None)
    assert json.loads(out.read_text())["one"]["kernel.auth"]["layer"] == "DEFAULT"
