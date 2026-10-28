"""
@file_name: test_distribution.py
@author: Bin Liang
@date: 2026-09-04
@description: narranexus-dist.json (spec section 19): parsing rejects bad shapes, resolution selects builtins by range and bundled plugins by path, names every problem (engine range, unknown plugin, excluded dependency, auth provider, foreign binding), the four official distributions resolve clean, and the lock is reproducible.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.contracts.distribution import DistributionError, parse_distribution
from narranexus.kernel.plugins.distribution import (
    LOCK_FILENAME,
    doctor_report,
    find_distribution,
    load_distribution,
    lock_data,
    resolve_distribution,
    resolve_from_env,
    write_lock,
)

REPO = Path(__file__).resolve().parents[3]
HOST = "1.15.0"


def _spec(**over):
    data = {
        "id": "acme.app",
        "displayName": "Acme",
        "engine": ">=1.0 <2",
        "plugins": {"builtin.chat": "^1.0", "builtin.basic_info": "^1.0", "builtin.auth.local": "^1.0"},
        "auth": "builtin.auth.local",
    }
    data.update(over)
    # every builtin must be selected or excluded (an unclassified one is a problem)
    from narranexus.kernel.plugins.builtins import builtin_manifests

    named = set(data["plugins"]) | set(data.get("excludes", []))
    data["excludes"] = list(data.get("excludes", [])) + [m.id for m in builtin_manifests() if m.id not in named]
    return parse_distribution(data)


def test_parse_normalises_plugin_refs_and_rejects_bad_shapes():
    spec = _spec(plugins={"builtin.chat": "^1.0", "acme.x": {"path": "./plugins/acme.x"}})
    assert spec.plugins["builtin.chat"].range == "^1.0" and not spec.plugins["builtin.chat"].bundled
    assert spec.plugins["acme.x"].bundled and spec.plugins["acme.x"].range == "*"
    with pytest.raises(DistributionError, match="id must be"):
        _spec(id="nodot")
    with pytest.raises(DistributionError, match="overlap"):
        _spec(excludes=["builtin.chat"])
    with pytest.raises(DistributionError, match="userPlugins must be false"):
        _spec(runtime={"deployment": "cloud", "userPlugins": True})
    with pytest.raises(DistributionError, match="disagrees"):
        _spec(bindings={"kernel.auth": "builtin.auth.netmind"})
    with pytest.raises(DistributionError, match="Extra inputs"):
        _spec(unknown_field=1)


def test_resolution_picks_builtins_and_names_every_problem():
    res = resolve_distribution(_spec(), Path("."), host_version=HOST)
    assert res.ok and res.plugin_ids == ("builtin.chat", "builtin.basic_info", "builtin.auth.local")
    assert res.bindings.entries["kernel.auth"] == "builtin.auth.local" and res.bindings.origin == "acme.app"

    bad = _spec(
        engine=">=9",
        plugins={"builtin.chat": "^1.0", "builtin.teams": "^1.0", "builtin.auth.local": ">=2", "nope.plugin": "*"},
        excludes=["builtin.message_bus", "ghost.builtin"],
        bindings={"turn.recall": "acme.missing"},
    )
    res = resolve_distribution(bad, Path("."), host_version=HOST)
    joined = "\n".join(res.problems)
    assert "engine: this host is 1.15.0" in joined
    assert "nope.plugin: unknown plugin" in joined
    assert "builtin.auth.local: engine ships 1.0.0, the distribution wants '>=2'" in joined
    assert "builtin.teams depends on builtin.message_bus, which is excluded" in joined
    assert "excludes: 'ghost.builtin' is not a builtin" in joined
    assert "auth: 'builtin.auth.local' is not in the distribution's plugins" in joined
    assert "bindings['turn.recall']: provider 'acme.missing' is not in the distribution" in joined
    with pytest.raises(DistributionError, match="acme.app: "):
        res.raise_for_problems()


def test_auth_must_be_a_distribution_only_provider_of_kernel_auth():
    res = resolve_distribution(_spec(auth="builtin.chat"), Path("."), host_version=HOST)
    assert res.problems == ["auth: 'builtin.chat' does not provide kernel.auth"]


def test_bundled_plugins_resolve_by_path(tmp_path: Path):
    plugin = tmp_path / "plugins" / "acme.x"
    (plugin / "backend").mkdir(parents=True)
    (plugin / "backend" / "__init__.py").write_text("def activate(ctx):\n    pass\n")
    (plugin / "narranexus-plugin.json").write_text(json.dumps({
        "id": "acme.x", "version": "0.2.0", "displayName": "X", "hosts": ["backend"], "backend": {"activate": True},
        "activationEvents": ["onStartup"],
    }))
    spec = _spec(plugins={**_spec().plugins, "acme.x": {"path": "./plugins/acme.x"}})
    res = resolve_distribution(spec, tmp_path, host_version=HOST)
    assert res.ok
    pick = res.pick("acme.x")
    assert pick is not None and pick.source == "path" and pick.path == plugin.resolve() and pick.version == "0.2.0"

    # a bundled path without a manifest, and a manifest that lies about its id
    (plugin / "narranexus-plugin.json").write_text(json.dumps({"id": "acme.other", "version": "0.2.0", "displayName": "X", "hosts": ["backend"]}))
    res = resolve_distribution(spec, tmp_path, host_version=HOST)
    assert res.problems == ["acme.x: bundled manifest declares id 'acme.other'"]
    spec = _spec(plugins={**_spec().plugins, "acme.y": {"path": "./plugins/acme.y"}})
    res = resolve_distribution(spec, tmp_path, host_version=HOST)
    assert res.problems == ["acme.y: bundled path './plugins/acme.y' has no narranexus-plugin.json"]


@pytest.mark.parametrize("name", ["desktop", "cloud", "minimal", "example-tob"])
def test_official_distributions_resolve_clean(name: str):
    spec, base = load_distribution(REPO / "distributions" / name)
    res = resolve_distribution(spec, base, host_version=HOST)
    assert res.problems == []
    report = doctor_report(res, host_version=HOST)
    assert report["ok"] and report["auth"] == spec.auth and report["engine"]["host"] == HOST
    if name == "cloud" or name == "example-tob":
        assert report["deployment"] == "cloud" and report["userPlugins"] is False


def test_lock_is_reproducible_and_relative(tmp_path: Path):
    spec, base = load_distribution(REPO / "distributions" / "example-tob")
    res = resolve_distribution(spec, base, host_version=HOST)
    data = lock_data(res, host_version=HOST)
    assert data["plugins"]["acme.crm"] == {"version": "0.1.0", "source": "path", "path": "plugins/acme.crm"}
    assert data["plugins"]["builtin.chat"]["source"] == "builtin" and data["auth"] == "acme.auth-sso"
    out = write_lock(res, tmp_path / LOCK_FILENAME, host_version=HOST)
    assert json.loads(out.read_text()) == data
    bad = resolve_distribution(_spec(engine=">=9"), base, host_version=HOST)
    with pytest.raises(DistributionError):
        write_lock(bad, tmp_path / "x.json", host_version=HOST)


def test_env_selects_the_distribution(tmp_path: Path):
    assert find_distribution({}) is None and resolve_from_env({}, host_version=HOST) is None
    res = resolve_from_env({"NARRANEXUS_DIST": str(REPO / "distributions" / "minimal")}, host_version=HOST)
    assert res is not None and res.spec.id == "narranexus.minimal" and "builtin.teams" in res.excluded
    (tmp_path / "narranexus-dist.json").write_text(json.dumps({"id": "acme.bad", "displayName": "b", "engine": ">=9"}))
    with pytest.raises(DistributionError, match="engine"):
        resolve_from_env({"NARRANEXUS_DIST": str(tmp_path)}, host_version=HOST)
    with pytest.raises(DistributionError, match="not found"):
        load_distribution(tmp_path / "missing")
