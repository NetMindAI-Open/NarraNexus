"""
@file_name: test_loader_contributions.py
@author: Bin Liang
@date: 2026-09-03
@description: The loader fills the declarative contribution registries from a manifest and registers hook impls under the manifest's id.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.contracts.route import RouterSpec
from narranexus.contracts.table import ColumnSpec, TableSpec
from narranexus.kernel.plugins.hooks import hookimpl
from narranexus.kernel.plugins.loader import load
from narranexus.kernel.plugins.manifest import parse_manifest
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution

MOD = "tests_fake_plugin_contribs"


@pytest.fixture
def fake_module(monkeypatch):
    mod = types.ModuleType(MOD)
    mod.ROUTES = (Contribution("api", lambda: RouterSpec(router=object(), prefix="/api/x/acme.weather")),)
    mod.TABLES = (
        Contribution(
            "items",
            lambda: TableSpec("ext_acme_weather__items", (ColumnSpec("id", "INTEGER", "BIGINT", primary_key=True),)),
        ),
    )
    calls: list[tuple[str, str]] = []

    @hookimpl("onDidPersistTurn")
    def after_turn(run_id, agent_id):
        calls.append((run_id, agent_id))

    @hookimpl("onDidStartRun", tryfirst=True)
    def on_start(run_id):
        calls.append(("start", run_id))

    mod.HOOKS = (after_turn, on_start)
    mod.BAD_HOOKS = (hookimpl("onDidNeverExist")(lambda: None),)
    mod.calls = calls
    monkeypatch.setitem(sys.modules, MOD, mod)
    return mod


def _manifest(registries: Registries, provides: dict) -> object:
    return parse_manifest(
        {
            "id": "acme.weather",
            "version": "1.0.0",
            "displayName": "Weather",
            "api": {"route": 0, "table": 0, "hook": 0},
            "hosts": ["backend"],
            "provides": provides,
        },
        tree=registries.slots,
    )


def test_routes_and_tables_land_in_their_registries_under_the_plugin_owner(fake_module):
    registries = Registries()
    manifest = _manifest(registries, {"backend.routes": [f"{MOD}:ROUTES"], "backend.tables": [f"{MOD}:TABLES"]})
    report = load(registries, [manifest], role="backend")
    assert not report.errors
    routes = registries.registry_for("backend.routes")
    assert routes.names() == ("api",)
    assert routes.entries()[0].owner == "acme.weather"
    assert routes.get("api").prefix == "/api/x/acme.weather"
    assert registries.registry_for("backend.tables").get("items").name == "ext_acme_weather__items"


def test_hook_impls_register_under_manifest_id_and_fire(fake_module):
    registries = Registries()  # host events are declared by the kernel
    manifest = _manifest(registries, {"backend.hooks": [f"{MOD}:HOOKS"]})
    report = load(registries, [manifest], role="backend")
    assert not report.errors
    assert report.loaded[0].entries == 2
    assert registries.hooks.caller("onDidPersistTurn").owners() == ("acme.weather",)
    asyncio.run(registries.hooks.caller("onDidPersistTurn").call(run_id="r1", agent_id="a1", user_id="u1", event_id="e1", narrative_ids=[]))
    assert fake_module.calls == [("r1", "a1")]


def test_undeclared_hook_isolates_a_user_plugin(fake_module):
    registries = Registries()
    manifest = _manifest(registries, {"backend.hooks": [f"{MOD}:BAD_HOOKS"]})
    report = load(registries, [manifest], role="backend")
    assert report.errors and UnknownEntry.__name__ in report.errors[0].error
