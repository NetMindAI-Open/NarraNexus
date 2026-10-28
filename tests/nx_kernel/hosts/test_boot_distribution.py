"""
@file_name: test_boot_distribution.py
@author: Bin Liang
@date: 2026-09-04
@description: Booting under a distribution: builtins outside it lose their contributions, bundled plugins boot in stage 1, runtime.userPlugins=false skips the registry, a distribution with problems refuses to boot, and a distribution-only plugin in registry.json is rejected at discovery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.contracts.distribution import DistributionError, parse_distribution
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.distribution import resolve_distribution
from narranexus.kernel.plugins.loader import discover
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.builtins import load_builtins

from .conftest import make_plugin, register

HOST = "1.15.0"


@pytest.fixture(autouse=True)
def _forget_bundled_packages():
    """Bundled plugins get a synthetic ``nxplugins.<id>`` package; each test must leave the process clean."""
    from narranexus.kernel.plugins.importer import uninstall_synthetic_package

    yield
    for pid in ("acme.crm", "acme.auth-sso", "acme.sso"):
        uninstall_synthetic_package(pid)
CORE = {"builtin.chat": "^1.0", "builtin.basic_info": "^1.0", "builtin.awareness": "^1.0", "builtin.providers": "^1.0",
        "builtin.frameworks.nexus_power": "^1.0", "builtin.turn": "^1.0", "builtin.llm_clients": "^1.0",
        "builtin.memory_kinds": "^1.0", "builtin.common_tools": "^1.0", "builtin.auth.local": "^1.0"}


def _dist(base: Path, **over):
    data = {"id": "acme.app", "displayName": "Acme", "engine": ">=1.0 <2", "plugins": dict(CORE), "auth": "builtin.auth.local"}
    data.update(over)
    # every builtin is selected or excluded (an unclassified one is a problem)
    from narranexus.kernel.plugins.builtins import builtin_manifests

    named = set(data["plugins"]) | set(data.get("excludes", []))
    data["excludes"] = list(data.get("excludes", [])) + [m.id for m in builtin_manifests() if m.id not in named]
    return resolve_distribution(parse_distribution(data), base, host_version=HOST)


def _owners(regs: Registries, slot: str) -> set[str]:
    return {e.owner for e in regs.registry_for(slot).entries()}


def test_distribution_drops_the_builtins_it_leaves_out(plugin_home: Path):
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("backend", registries=regs, cloud=False, host_version=HOST, distribution=_dist(plugin_home))
    assert report.distribution == "acme.app" and "builtin.teams" in report.excluded_builtins
    assert "builtin.teams" not in _owners(regs, "backend.routes") and "builtin.chat" in _owners(regs, "backend.routes")
    assert {pl.plugin_id for pl in report.builtins.loaded} == set(CORE) and not report.builtins.errors
    assert _owners(regs, "kernel.auth") == {"builtin.auth.local"}


def test_bundled_plugin_boots_in_stage_one_and_user_plugins_are_gated(plugin_home: Path):
    bundled = make_plugin(plugin_home / "dist", "acme.crm", min_app="1.0.0")
    user = make_plugin(plugin_home, "acme.user")
    store = register(plugin_home, "acme.user", user)
    res = _dist(plugin_home / "dist", plugins={**CORE, "acme.crm": {"path": "./acme.crm"}},
                runtime={"deployment": "cloud", "userPlugins": False})
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("backend", registries=regs, cloud=False, host_version=HOST, store=store, distribution=res)
    loaded = {pl.plugin_id for pl in report.builtins.loaded}
    assert "acme.crm" in loaded and report.users is None and report.user_plugin_ids == ()
    assert bundled.exists()


def test_a_broken_distribution_refuses_to_boot(plugin_home: Path):
    res = _dist(plugin_home, engine=">=9")
    regs = Registries()
    load_builtins(regs, "backend")
    with pytest.raises(DistributionError, match="engine"):
        boot("backend", registries=regs, cloud=False, host_version=HOST, distribution=res)


def test_distribution_only_plugin_is_rejected_at_runtime_install(plugin_home: Path):
    path = make_plugin(plugin_home, "acme.sso", extra={
        "api": {"auth": 0}, "distributionOnly": True, "backend": {"activate": False},
        "provides": {"kernel.auth": "nxplugins.acme_sso:CONTRIBUTION"}, "activationEvents": [],
    })
    (path / "backend" / "__init__.py").write_text(
        "from narranexus.kernel.plugins.registry import Contribution\nCONTRIBUTION = Contribution('sso', lambda: object())\n"
    )
    store = register(plugin_home, "acme.sso", path)
    found = discover(cloud=False, user_registry_path=store.path, host_version=HOST)
    assert found.rejected["acme.sso"].startswith("incompatible: distribution-only plugin")
    assert "acme.sso" not in {m.id for m in found.manifests}
    # the same manifest is accepted when it arrives through a distribution
    res = _dist(plugin_home, plugins={**CORE, "acme.sso": {"path": "./acme.sso"}}, auth="acme.sso")
    assert res.ok and res.pick("acme.sso").manifest.distribution_only


def test_lock_matches_the_doctor_view(plugin_home: Path):
    from narranexus.kernel.plugins.distribution import doctor_report, lock_data

    res = _dist(plugin_home)
    doc = doctor_report(res, host_version=HOST)
    lock = lock_data(res, host_version=HOST)
    assert {r["id"] for r in doc["plugins"]} == set(lock["plugins"]) and doc["bindings"] == lock["bindings"]
    assert json.dumps(lock, sort_keys=True)  # serialisable


def test_a_bundled_plugin_gets_its_lifecycle_like_a_user_plugin(plugin_home: Path):
    """A distribution's bundled plugin is stage-1 code but carries tables and
    activate(ctx): it must be registered with the activator (and its tables
    registered) — the shipped example distribution's acme.crm activates on
    onStartup only because of this."""
    body = (
        "from narranexus.contracts.table import ColumnSpec, TableSpec\n"
        "from narranexus.kernel.plugins.registry import Contribution\n"
        "TABLES = (Contribution('items', lambda: TableSpec('ext_acme_crm__items', (ColumnSpec('id', 'TEXT', 'VARCHAR(64)', primary_key=True),))),)\n"
        "def activate(ctx):\n    pass\n"
    )
    make_plugin(plugin_home / "dist", "acme.crm", min_app="1.0.0", body=body, extra={"provides": {"backend.tables": ["nxplugins.acme_crm:TABLES"]}, "api": {"table": 0}})
    res = _dist(plugin_home / "dist", plugins={**CORE, "acme.crm": {"path": "./acme.crm"}})
    regs = Registries()
    load_builtins(regs, "backend")
    registered_events: dict[str, tuple[str, ...]] = {}

    class _Activator:
        def register(self, manifest):
            registered_events[manifest.id] = ("onStartup",)
            return ("onStartup",)

    tables: list[str] = []
    report = boot("backend", registries=regs, cloud=False, host_version=HOST, distribution=res, activator=_Activator(), register_table=lambda spec, owner: tables.append(owner))
    assert report.activation_events == {"acme.crm": ("onStartup",)} and tables == ["acme.crm"]
    assert report.isolated == {}


def test_a_corrupt_registry_does_not_keep_a_distribution_from_booting_its_bundle(plugin_home: Path):
    make_plugin(plugin_home / "dist", "acme.crm", min_app="1.0.0")
    res = _dist(plugin_home / "dist", plugins={**CORE, "acme.crm": {"path": "./acme.crm"}})
    (plugin_home / "registry.json").write_text("{not json")
    from narranexus.kernel.plugins.lifecycle import RegistryStore

    store = RegistryStore(path=plugin_home / "registry.json", lkg=plugin_home / "registry.lkg.json")
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("backend", registries=regs, cloud=False, host_version=HOST, store=store, distribution=res)
    assert "acme.crm" in {pl.plugin_id for pl in report.builtins.loaded} and not report.builtins.errors
