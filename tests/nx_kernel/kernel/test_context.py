"""
@file_name: test_context.py
@author: Bin Liang
@date: 2026-09-03
@description: PluginContext confines db to ext_<id>_ tables, registries to provided slots, events to owned subscriptions, and unwinds on dispose.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from narranexus.contracts import IncompatibleProvider, PluginError
from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.contracts.table import table_prefix_for
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.context import build_context
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.services import ServiceLocator
from narranexus.kernel.settings import PluginSettings


class _Db:
    def __init__(self):
        self.calls = []

    async def get(self, table, filters, **kw):
        self.calls.append(("get", table, filters))
        return []

    async def get_one(self, table, filters):
        self.calls.append(("get_one", table, filters))
        return None

    async def insert(self, table, data):
        self.calls.append(("insert", table, data))
        return 1

    async def update(self, table, filters, data):
        return 1

    async def delete(self, table, filters):
        return 1


def _ctx(plugin_id="acme.weather", provides=("backend.routes",), bus=None):
    return build_context(
        plugin_id=plugin_id,
        version="1.0.0",
        path=Path("/p"),
        role="backend",
        host_version="1.19.0",
        registries=Registries(),
        provides=provides,
        settings=PluginSettings(plugin_id, SettingsSchema({"k": SettingField("string", default="v")}), environ={}),
        db_client=_Db(),
        bus=bus or EventBus(),
        services=ServiceLocator().scoped(plugin_id),
    )


def test_db_is_confined_to_the_plugin_prefix():
    ctx = _ctx()
    asyncio.run(ctx.db.insert("ext_acme_weather__items", {"a": 1}))
    with pytest.raises(PluginError, match="outside ext_acme_weather__"):
        asyncio.run(ctx.db.get("events", {}))
    builtin = _ctx("builtin.chat")
    asyncio.run(builtin.db.get("events", {}))  # builtins keep core access


def test_a_plugin_cannot_reach_a_sibling_whose_id_extends_its_own():
    """``table_prefix_for``'s docstring names this exploit: with a SINGLE
    underscore terminator, ``acme.weather``'s prefix ``ext_acme_weather_`` is a
    prefix of every table of ``acme.weather_x``, so the confinement check would
    let the first plugin read and write the second one's rows. The negative case
    has to be a real sibling table name — asserting on the error string alone
    stays green when the terminator changes."""
    sibling_table = table_prefix_for("acme.weather_x") + "items"
    assert sibling_table.startswith("ext_acme_weather_")  # the tempting near-miss
    ctx = _ctx("acme.weather")
    for call in (
        lambda: ctx.db.get(sibling_table, {}),
        lambda: ctx.db.insert(sibling_table, {"a": 1}),
    ):
        with pytest.raises(PluginError, match="outside ext_acme_weather__"):
            asyncio.run(call())
    # and the sibling reaches its own rows, so the guard is not simply refusing everything
    asyncio.run(_ctx("acme.weather_x").db.insert(sibling_table, {"a": 1}))


def test_registries_are_scoped_to_provides_and_api_versions_checked():
    ctx = _ctx(provides=("backend.routes",))
    assert ctx.registries.registry_for("backend.routes").kind == "backend.routes"
    with pytest.raises(PluginError, match="not in the manifest"):
        ctx.registries.registry_for("backend.tables")
    # api_version("route") == 0 is today's constant on both sides; what has
    # teeth is that a plugin asking for a version this host does not have is
    # refused, and that an unknown kind is not silently version 0.
    ctx.require_api("route", ctx.api_version("route"))
    with pytest.raises(IncompatibleProvider):
        ctx.require_api("route", 5)
    with pytest.raises(PluginError, match="unknown contract kind"):
        ctx.api_version("nope")
    assert ctx.settings.get("k") == "v" and ctx.log is not None and ctx.data_dir == Path("/p/data")


def test_event_subscriptions_are_owned_and_disposed_with_the_context():
    bus = EventBus()
    ctx = _ctx(bus=bus)
    seen = []
    ctx.events.subscribe("onDidPersistTurn", lambda payload: seen.append(payload["run_id"]))
    assert bus.subscriber_count("onDidPersistTurn") == 1
    asyncio.run(bus.emit("onDidPersistTurn", {"run_id": "r1"}))
    assert seen == ["r1"]
    ctx.dispose()
    assert bus.subscriber_count("onDidPersistTurn") == 0
    assert ctx.subscriptions.disposed
    bus.close()
