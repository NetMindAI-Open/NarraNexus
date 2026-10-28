"""
@file_name: test_plugin_settings_store_private_loop.py
@author: Bin Liang
@date: 2026-09-04
@description: Found running the stack: a user plugin's activation failed with "asyncio Event is bound to a different event loop" because the sync SettingsStore drove the host loop's database client from another loop. Without an injected client the store now runs on its own long-lived loop with that loop's own client, so load/save/delete work from the host's event-loop thread, from plain sync code, and never touch the host client; an injected client keeps the old behaviour.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.utils.db import plugin_settings_store as mod
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend

_SHARED = {"path": None}


async def _fresh_client():
    backend = SQLiteBackend(_SHARED["path"])
    await backend.initialize()
    await auto_migrate(backend)
    return await AsyncDatabaseClient.create_with_backend(backend)


@pytest.fixture
def private_db(tmp_path, monkeypatch):
    _SHARED["path"] = str(tmp_path / "settings.db")
    built = {}

    async def get_db_client():
        loop = asyncio.get_running_loop()
        if id(loop) not in built:
            built[id(loop)] = await _fresh_client()
        return built[id(loop)]

    import narranexus.platform.utils.db.db_factory as f

    monkeypatch.setattr(f, "get_db_client", get_db_client)
    monkeypatch.setattr(mod, "_PRIVATE", mod._PrivateLoop())
    return built


@pytest.mark.asyncio
async def test_sync_store_works_from_inside_the_host_loop_without_touching_its_client(private_db):
    store = mod.DbSettingsStore()  # host style: no injected client
    host_loop = asyncio.get_running_loop()
    assert store.load("acme.x") == {}
    store.save("acme.x", "greeting", "hi", secret=False)
    store.save("acme.x", "token", "s3cret", secret=True)
    assert store.load("acme.x") == {"greeting": "hi", "token": "s3cret"}
    store.delete("acme.x", "greeting")
    assert store.load("acme.x") == {"token": "s3cret"}
    assert id(host_loop) not in private_db and len(private_db) == 1  # only the private loop built a client


def test_sync_store_works_from_plain_sync_code(private_db):
    store = mod.DbSettingsStore()
    store.save("acme.y", "k", 1, secret=False)
    assert store.load("acme.y") == {"k": 1}


@pytest.mark.asyncio
async def test_injected_client_keeps_the_async_api(db_client):
    store = mod.DbSettingsStore(db_client)
    await store.save_async("acme.z", "k", [1, 2], secret=False)
    assert await store.load_async("acme.z") == {"k": [1, 2]}
