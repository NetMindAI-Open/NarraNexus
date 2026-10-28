"""
@file_name: test_boot.py
@author: Bin Liang
@date: 2026-09-03
@description: Staged boot: builtins fail-fast, user plugins isolate, rejected plugins get their state, safe mode after two dead boots, registries freeze.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narranexus.contracts import PluginError
from narranexus.contracts.route import RouterSpec
from narranexus.contracts.settings import SettingsSchema
from narranexus.contracts.table import ColumnSpec, TableSpec
from narranexus.hosts.boot import boot
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.context import build_context
from narranexus.kernel.plugins.lifecycle import BootMarker, RegistryStore
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.services import ServiceLocator
from narranexus.kernel.settings import PluginSettings

from .conftest import make_plugin, register


def _activator(registries: Registries, store: RegistryStore) -> Activator:
    def factory(manifest):
        return build_context(
            plugin_id=manifest.id, version=manifest.version, path=Path("/p"), role="backend", host_version="1.19.0",
            registries=registries, provides=tuple(manifest.provides), settings=PluginSettings(manifest.id, SettingsSchema(), environ={}),
            db_client=None, bus=EventBus(), services=ServiceLocator().scoped(manifest.id),
        )

    return Activator(context_factory=factory, on_crash=lambda pid, err: store.record_crash(pid, err))


def test_user_plugin_loads_registers_events_and_advances_state(plugin_home: Path):
    path = make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", path)
    registries = Registries()
    act = _activator(registries, store)
    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", activator=act, store=store)
    assert report.user_plugin_ids == ("acme.ok",) and not report.safe_mode and report.isolated == {}
    assert report.activation_events == {"acme.ok": ("onStartup",)}
    assert store.read().plugins["acme.ok"].state == "enabled"
    assert registries.frozen and report.builtins is not None and not report.builtins.errors
    assert (plugin_home / ".booting-backend").exists()
    report.mark_healthy()
    assert not (plugin_home / ".booting-backend").exists()
    (result,) = asyncio.run(act.fire("onStartup"))
    assert result.error is None and act.is_active("acme.ok")


def test_cloud_loads_builtins_only(plugin_home: Path):
    path = make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", path)
    registries = Registries()
    report = boot("backend", registries=registries, cloud=True, host_version="1.19.0", store=store)
    assert report.user_plugin_ids == () and report.users is None
    assert not (plugin_home / ".booting-backend").exists()  # no marker on cloud


def test_rejections_are_recorded_with_their_state(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok", min_app="9.0.0")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    store.register("acme.gone", __import__("narranexus.kernel.plugins.lifecycle", fromlist=["PluginRecord"]).PluginRecord(path=str(plugin_home / "nope")))
    make_plugin(plugin_home, "acme.bad", version="0.9.0")
    store.register("acme.bad", __import__("narranexus.kernel.plugins.lifecycle", fromlist=["PluginRecord"]).PluginRecord(path=str(plugin_home / "acme.bad")))
    registries = Registries()
    report = boot(
        "backend", registries=registries, cloud=False, host_version="1.19.0", store=store,
        blocked_versions={"acme.bad": {"below": "1.0.0", "reason": "leaks credentials"}},
    )
    assert report.user_plugin_ids == ()
    reg = store.read()
    assert reg.plugins["acme.ok"].state == "incompatible" and "minAppVersion" in (reg.plugins["acme.ok"].last_error or "")
    assert reg.plugins["acme.gone"].state == "missing"
    assert reg.plugins["acme.bad"].state == "blocked" and "leaks credentials" in (reg.plugins["acme.bad"].last_error or "")


def test_broken_user_plugin_is_isolated_and_counted(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    make_plugin(plugin_home, "acme.bad", extra={"provides": {"backend.routes": ["nxplugins.acme_bad:NOPE"]}, "api": {"route": 0}})
    store.register("acme.bad", __import__("narranexus.kernel.plugins.lifecycle", fromlist=["PluginRecord"]).PluginRecord(path=str(plugin_home / "acme.bad")))
    registries = Registries()
    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
    assert report.user_plugin_ids == ("acme.ok",)
    assert "acme.bad" in report.isolated
    assert store.read().plugins["acme.bad"].crash_count == 1 and store.read().plugins["acme.ok"].state == "enabled"


def test_plugin_tables_are_registered_before_migration(plugin_home: Path):
    make_plugin(
        plugin_home, "acme.tables",
        body="from narranexus.contracts.table import ColumnSpec, TableSpec\nfrom narranexus.kernel.plugins.registry import Contribution\n"
             "TABLES = (Contribution('items', lambda: TableSpec('ext_acme_tables__items', (ColumnSpec('id', 'INTEGER', 'BIGINT', primary_key=True),))),)\n"
             "def activate(ctx):\n    pass\n",
        extra={"provides": {"backend.tables": ["nxplugins.acme_tables:TABLES"]}, "api": {"table": 0}},
    )
    store = register(plugin_home, "acme.tables", plugin_home / "acme.tables")
    registered = []
    registries = Registries()
    boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store, register_table=lambda spec, owner: registered.append((spec.name, owner)))
    assert registered == [("ext_acme_tables__items", "acme.tables")]


def test_builtin_failure_is_fatal(plugin_home: Path, monkeypatch):
    import narranexus.hosts.boot as boot_mod

    def broken_load(registries, manifests, *, role):
        raise PluginError("builtin exploded")

    monkeypatch.setattr(boot_mod, "load", broken_load)
    with pytest.raises(PluginError, match="builtin exploded"):
        boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=RegistryStore(path=plugin_home / "registry.json"))


def test_two_dead_boots_flip_safe_mode_and_skip_user_plugins(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    # two boots that never reached health
    BootMarker("backend").enter()
    BootMarker("backend").enter()
    registries = Registries()
    report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
    assert report.safe_mode and "consecutive boots" in report.safe_mode_reason
    assert report.user_plugin_ids == () and store.read().safe_mode
    # once the operator leaves safe mode and the boot is healthy, plugins load again
    store.set_safe_mode(False)
    report.mark_healthy()
    registries2 = Registries()
    report2 = boot("backend", registries=registries2, cloud=False, host_version="1.19.0", store=store)
    assert not report2.safe_mode and report2.user_plugin_ids == ("acme.ok",)


def test_dependency_order_is_respected_at_boot(plugin_home: Path):
    make_plugin(plugin_home, "acme.ok")
    make_plugin(plugin_home, "acme.dep", extra={"dependencies": {"acme.ok": ">=1.0"}})
    store = register(plugin_home, "acme.dep", plugin_home / "acme.dep")
    store.register("acme.ok", __import__("narranexus.kernel.plugins.lifecycle", fromlist=["PluginRecord"]).PluginRecord(path=str(plugin_home / "acme.ok")))
    report = boot("backend", registries=Registries(), cloud=False, host_version="1.19.0", store=store)
    assert report.user_plugin_ids == ("acme.ok", "acme.dep")


def test_contribution_from_user_plugin_is_visible_in_registries(plugin_home: Path):
    make_plugin(
        plugin_home, "acme.ok",
        body="from narranexus.contracts.route import RouterSpec\nfrom narranexus.kernel.plugins.registry import Contribution\n"
             "ROUTES = (Contribution('api', lambda: RouterSpec(object(), '/api/x/acme.ok')),)\n",
        extra={"provides": {"backend.routes": ["nxplugins.acme_ok:ROUTES"]}, "api": {"route": 0}, "backend": {"activate": False}},
    )
    store = register(plugin_home, "acme.ok", plugin_home / "acme.ok")
    registries = Registries()
    boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
    routes = registries.registry_for("backend.routes")
    assert tuple(e.name for e in routes.entries() if e.owner == "acme.ok") == ("api",)
    acme_entry = next(e for e in routes.entries() if e.owner == "acme.ok")
    # Guard: the loader must register the Contribution's LAZY FACTORY, not the
    # RouterSpec object it produces. A loader bug that stuffs the spec itself
    # in as `factory=` would make `entry.factory` uncallable (or callable but
    # returning something other than a RouterSpec) — the entries()[0] index
    # this used to check picked an unrelated builtin entry and could never
    # have caught that regardless.
    assert callable(acme_entry.factory)
    assert isinstance(acme_entry.factory(), RouterSpec)
