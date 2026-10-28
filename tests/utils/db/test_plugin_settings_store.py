"""
@file_name: test_plugin_settings_store.py
@author: Bin Liang
@date: 2026-09-03
@description: DbSettingsStore persists plugin settings rows, encrypts secrets at rest and round-trips them through PluginSettings.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from narranexus.contracts.settings import SettingField, SettingsSchema
from narranexus.kernel.settings import PluginSettings
from narranexus.platform.marketplace._skill_marketplace_impl.secret_box import SecretBox
from narranexus.platform.utils.db.plugin_settings_store import DbSettingsStore

SCHEMA = SettingsSchema({"api_key": SettingField("string", secret=True), "retries": SettingField("integer", default=3)})


@pytest.mark.asyncio
async def test_secret_rows_are_ciphertext_and_round_trip(db_client):
    store = DbSettingsStore(db_client, secret_box=SecretBox(Fernet.generate_key()))
    await store.save_async("acme.weather", "api_key", "sk-live-1", secret=True)
    await store.save_async("acme.weather", "retries", 7, secret=False)
    rows = await db_client.get("plugin_settings", {"plugin_id": "acme.weather"})
    by_key = {r["key"]: r for r in rows}
    assert by_key["api_key"]["is_secret"] == 1 and "sk-live-1" not in by_key["api_key"]["value_json"]
    assert by_key["api_key"]["value_json"].startswith(SecretBox.TOKEN_PREFIX)
    assert by_key["retries"]["value_json"] == "7"
    loaded = await store.load_async("acme.weather")
    assert loaded == {"api_key": "sk-live-1", "retries": 7}
    # update path and delete path
    await store.save_async("acme.weather", "retries", 8, secret=False)
    assert (await store.load_async("acme.weather"))["retries"] == 8
    await store.delete_async("acme.weather", "retries")
    assert "retries" not in await store.load_async("acme.weather")


def test_sync_bridge_from_no_running_loop(tmp_path):
    """PluginSettings (sync) over the DB store when no event loop runs (CLI / boot paths)."""
    import asyncio

    from narranexus.platform.utils.db.database import AsyncDatabaseClient
    from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    class _Client:
        """Fresh client per call: each asyncio.run() gets its own loop."""

        def __init__(self, path):
            self._path = path

        async def _client(self):
            backend = SQLiteBackend(str(self._path))
            await backend.initialize()
            await auto_migrate(backend)
            return await AsyncDatabaseClient.create_with_backend(backend)

        async def get(self, *a, **k):
            c = await self._client()
            try:
                return await c.get(*a, **k)
            finally:
                await c.close()

        async def get_one(self, *a, **k):
            c = await self._client()
            try:
                return await c.get_one(*a, **k)
            finally:
                await c.close()

        async def insert(self, *a, **k):
            c = await self._client()
            try:
                return await c.insert(*a, **k)
            finally:
                await c.close()

        async def update(self, *a, **k):
            c = await self._client()
            try:
                return await c.update(*a, **k)
            finally:
                await c.close()

        async def delete(self, *a, **k):
            c = await self._client()
            try:
                return await c.delete(*a, **k)
            finally:
                await c.close()

    store = DbSettingsStore(_Client(tmp_path / "s.db"), secret_box=SecretBox(Fernet.generate_key()))
    settings = PluginSettings("acme.weather", SCHEMA, store=store, environ={})
    settings.set("api_key", "sk-2")
    settings.set("retries", 9)
    again = PluginSettings("acme.weather", SCHEMA, store=store, environ={})
    assert again.get("api_key") == "sk-2"
    assert again.get("retries") == 9

    # The claim under test: the sync bridge (`_run()` in
    # plugin_settings_store.py, called with no running loop) must create and
    # tear down its OWN private loop per call, never leaking a
    # thread/loop or leaving this thread's ambient asyncio state broken.
    # Prove it two ways: (1) repeated calls in a tight loop don't accumulate
    # threads — a regression that started spawning a worker thread per call
    # (e.g. always routing through the "inside a running loop" hop) would show
    # up as thread growth; (2) an independent asyncio.run() driven right
    # after the bridge calls still works — a regression that left a running
    # or closed loop registered as this thread's default would break it.
    import threading

    # Compare thread IDENTITIES, not the global count: the count also moves
    # when an unrelated earlier thread exits mid-loop (an aiosqlite connection
    # thread winding down after its test closed it), which is not a leak and
    # made this flake in CI. A leak is a thread created during these calls
    # that is still alive afterwards.
    before = set(threading.enumerate())
    for i in range(10):
        settings.set("retries", i)
    leaked = [t.name for t in threading.enumerate() if t not in before]
    assert not leaked, f"sync bridge leaked threads: {leaked}"

    async def _probe() -> str:
        return "ok"

    assert asyncio.run(_probe()) == "ok"
