"""
@file_name: test_builtin_manifests_on_disk.py
@author: Bin Liang
@date: 2026-09-07
@description: The builtin manifests have ONE home — plugins/<id>/narranexus-plugin.json — and the kernel's BUILTIN_PLUGINS names every plugin directory exactly once, in load order, with the package its manifest ships in; every manifest validates against the kernel tree and its id is its directory.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.kernel.plugins.builtins import BUILTIN_MANIFEST_DATA, BUILTIN_PLUGINS, builtin_manifests, slot_tree_with_builtins
from narranexus.kernel.plugins.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[3]


def test_every_plugin_directory_is_listed_once_with_its_package():
    dirs = sorted(p.name for p in (REPO / "plugins").iterdir() if p.is_dir() and p.name.startswith("builtin."))
    listed = [pid for pid, _ in BUILTIN_PLUGINS]
    assert sorted(listed) == dirs and len(listed) == len(set(listed))
    for pid, pkg in BUILTIN_PLUGINS:
        assert (REPO / "plugins" / pid / "src" / "narranexus_plugins" / pkg).is_dir(), (pid, pkg)


def test_manifests_come_from_disk_and_validate():
    # Against the tree WITH every builtin's declarations: a framework plugin
    # provides into the slot builtin.turn declares.
    tree = slot_tree_with_builtins()
    for pid, _ in BUILTIN_PLUGINS:
        on_disk = json.loads((REPO / "plugins" / pid / "narranexus-plugin.json").read_text())
        assert on_disk["id"] == pid
        assert on_disk in BUILTIN_MANIFEST_DATA
        parse_manifest(on_disk, tree=tree, allow_builtin=True)
    assert [m.id for m in builtin_manifests()] == [pid for pid, _ in BUILTIN_PLUGINS]


def test_every_plugin_wheel_carries_its_manifest():
    import tomllib

    for pid, pkg in BUILTIN_PLUGINS:
        cfg = tomllib.loads((REPO / "plugins" / pid / "pyproject.toml").read_text())
        include = cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
        assert include["narranexus-plugin.json"] == f"narranexus_plugins/{pkg}/narranexus-plugin.json", pid


def _ids() -> list[str]:
    return [pid for pid, _ in BUILTIN_PLUGINS]


def test_every_listing_of_the_builtins_agrees_with_the_plugin_directories():
    """The builtin set is written down in several places (each with its own
    reason to exist); none may drift from ``plugins/``: the uv workspace
    dependencies and sources, pyright's include list, the uv lock, and every
    official distribution (which must classify each builtin as shipped or
    excluded)."""
    import tomllib

    ids = _ids()
    dist_names = {pid: tomllib.loads((REPO / "plugins" / pid / "pyproject.toml").read_text())["project"]["name"] for pid in ids}
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
    deps = {d for d in pyproject["project"]["dependencies"] if d.startswith("narranexus-plugin-")}
    assert deps == set(dist_names.values())
    sources = {name for name, spec in pyproject["tool"]["uv"]["sources"].items() if name.startswith("narranexus-plugin-")}
    assert sources == set(dist_names.values())
    assert all(spec == {"workspace": True} for name, spec in pyproject["tool"]["uv"]["sources"].items() if name in sources)
    lock = (REPO / "uv.lock").read_text()
    assert all(f'name = "{name}"' in lock for name in dist_names.values())
    pyright = (REPO / "pyrightconfig.json").read_text()  # commented JSON — substring check
    assert all(f'"plugins/{pid}/src"' in pyright for pid in ids)
    # pytest's import path: a plugin missing here fails its own package test as a
    # COLLECTION ERROR, not an assertion — a failure shape nobody recognises.
    pythonpath = set(pyproject["tool"]["pytest"]["ini_options"]["pythonpath"])
    assert {f"plugins/{pid}/src" for pid in ids} <= pythonpath, sorted({f"plugins/{pid}/src" for pid in ids} - pythonpath)
    # import-linter: "builtin packages are independent" is the machinery behind
    # the hard rule that builtins never import each other (spec section 19.1);
    # a package missing from its ``modules`` list is simply not governed.
    contracts = pyproject["tool"]["importlinter"]["contracts"]
    independence = [c for c in contracts if c["name"].startswith("builtin packages are independent")]
    assert len(independence) == 1, [c["name"] for c in contracts]
    packages = {pkg for _, pkg in BUILTIN_PLUGINS}
    assert {f"narranexus_plugins.{pkg}" for pkg in packages} == set(independence[0]["modules"])
    for dist_dir in sorted(p for p in (REPO / "distributions").iterdir() if p.is_dir()):
        spec = json.loads((dist_dir / "narranexus-dist.json").read_text())
        classified = set(spec["plugins"]) | set(spec.get("excludes", []))
        # A distribution may ship third-party plugins too; every BUILTIN must be classified.
        assert {p for p in classified if p.startswith("builtin.")} == set(ids), dist_dir.name
        assert not (set(spec["plugins"]) & set(spec.get("excludes", []))), dist_dir.name


def test_a_builtin_whose_package_is_absent_is_skipped_not_fatal(monkeypatch):
    """A wheel-based distribution ships a subset of the builtins: the engine must
    import without the others, and say which are missing."""
    from narranexus.kernel.plugins import builtins as mod

    real = mod._manifest_path

    def _missing(plugin_id, package):
        if plugin_id == "builtin.teams":
            raise FileNotFoundError("not shipped")
        return real(plugin_id, package)

    monkeypatch.setattr(mod, "_manifest_path", _missing)
    mod._manifest_data_and_missing.cache_clear()
    try:
        assert mod.missing_builtins() == ("builtin.teams",)
        assert "builtin.teams" not in {d["id"] for d in mod.builtin_manifest_data()}
        assert len(mod.builtin_manifest_data()) == len(BUILTIN_PLUGINS) - 1
    finally:
        monkeypatch.undo()
        mod._manifest_data_and_missing.cache_clear()
    assert mod.missing_builtins() == ()


def test_every_builtin_manifest_versions_exactly_the_contract_kinds_it_fills():
    """``api`` is the fail-closed contract gate (docs/API_POLICY.md section 5) and
    it only checks the kinds it is given. A builtin that provides into a
    ``framework``/``provider``/``services`` slot without an ``api`` entry for
    that kind survives a contract bump silently and then fails deep inside a
    turn. Builtins are also the template third parties copy, so the set must be
    EXACT: a kind the plugin does not fill (three channels claimed ``route``,
    ``builtin.turn`` claimed ``agent``) teaches the wrong shape."""
    from narranexus.kernel.plugins.manifest import Manifest, slot_kinds_of

    tree = slot_tree_with_builtins()
    mismatched = {}
    for data in BUILTIN_MANIFEST_DATA:
        manifest = Manifest.model_validate(data)
        expected = set(slot_kinds_of(manifest, tree))
        if expected != set(manifest.api):
            mismatched[manifest.id] = {"missing": sorted(expected - set(manifest.api)), "spurious": sorted(set(manifest.api) - expected)}
    assert mismatched == {}, mismatched
    assert len(BUILTIN_MANIFEST_DATA) == len(BUILTIN_PLUGINS)  # the loop above ran over every builtin
