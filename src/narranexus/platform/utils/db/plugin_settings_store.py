"""
@file_name: plugin_settings_store.py
@author: Bin Liang
@date: 2026-09-03
@description: Database-backed ``SettingsStore`` for plugin settings (table ``plugin_settings``), secrets Fernet-encrypted.

The kernel's ``PluginSettings`` is synchronous over a snapshot; this store
bridges to the async database client by running its statements on the
client's loop (``run_coroutine_threadsafe`` when called from another thread,
direct ``run_until_complete`` on a private loop when no loop is running).
Secrets are encrypted with the platform ``SecretBox`` before they hit the
row and decrypted on load, so a database dump never contains a plaintext
plugin credential.
"""
from __future__ import annotations

import asyncio
import threading
import json

from narranexus.platform.utils.timezone import utc_now
from typing import Any, Awaitable, Mapping, TypeVar

from narranexus.platform.marketplace._skill_marketplace_impl.secret_box import SecretBox, get_secret_box

T = TypeVar("T")

TABLE = "plugin_settings"


def _run(coro: Awaitable[T]) -> T:
    """Drive ``coro`` to completion from sync code, wherever we are (no running loop, or inside one)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(coro)  # type: ignore[arg-type]
    # Called from inside a running loop's thread: the caller must not block the
    # loop, so hop to a worker thread that drives a private loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()  # type: ignore[arg-type]


class _PrivateLoop:
    """One long-lived thread driving its own event loop, with its own per-loop database client.

    The sync ``SettingsStore`` protocol is called from the host's event-loop thread
    (plugin activation builds ``PluginSettings`` there) — an async client bound to
    the host loop cannot be driven from another loop (its locks/events belong to
    the host loop), and the host loop cannot block on itself. So the store's
    coroutines run here: ``get_db_client()`` builds a client for THIS loop once.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="plugin-settings-db", daemon=True).start()
                self._loop = loop
            return self._loop

    def run(self, coro: Awaitable[T]) -> T:
        loop = self.loop()
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is loop:
            raise RuntimeError("plugin settings: sync store used from its own private loop")
        return asyncio.run_coroutine_threadsafe(coro, loop).result()  # type: ignore[arg-type]


_PRIVATE = _PrivateLoop()


class DbSettingsStore:
    """``SettingsStore`` over ``AsyncDatabaseClient``."""

    def __init__(self, db: Any = None, *, secret_box: SecretBox | None = None) -> None:
        # ``db`` given (tests, or a caller that owns a loop-bound client): the async
        # methods use it and the sync facade drives them on a throwaway loop.
        # ``db`` None (the backend host): every call runs on the private loop and
        # uses that loop's own client — safe from the host loop thread.
        self._db = db
        self._box = secret_box

    async def _client(self) -> Any:
        if self._db is not None:
            return self._db
        from narranexus.platform.utils.db.db_factory import get_db_client

        return await get_db_client()

    def _secret_box(self) -> SecretBox:
        if self._box is None:
            self._box = get_secret_box()
        return self._box

    async def load_async(self, plugin_id: str) -> dict[str, Any]:
        db = await self._client()
        rows = await db.get(TABLE, {"plugin_id": plugin_id})
        out: dict[str, Any] = {}
        for row in rows:
            raw = row.get("value_json")
            if raw is None:
                continue
            if int(row.get("is_secret") or 0):
                out[row["key"]] = self._secret_box().decrypt(raw)
            else:
                out[row["key"]] = json.loads(raw)
        return out

    async def save_async(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None:
        db = await self._client()
        stored = self._secret_box().encrypt(str(value)) if secret else json.dumps(value)
        existing = await db.get_one(TABLE, {"plugin_id": plugin_id, "key": key})
        data = {"value_json": stored, "is_secret": 1 if secret else 0}
        if existing:
            await db.update(TABLE, {"plugin_id": plugin_id, "key": key}, {**data, "updated_at": utc_now()})
        else:
            await db.insert(TABLE, {"plugin_id": plugin_id, "key": key, **data})

    async def delete_async(self, plugin_id: str, key: str) -> None:
        db = await self._client()
        await db.delete(TABLE, {"plugin_id": plugin_id, "key": key})

    def _drive(self, coro: Awaitable[T]) -> T:
        return _run(coro) if self._db is not None else _PRIVATE.run(coro)

    # ---- SettingsStore (sync) protocol

    def load(self, plugin_id: str) -> Mapping[str, Any]:
        return self._drive(self.load_async(plugin_id))

    def save(self, plugin_id: str, key: str, value: Any, *, secret: bool) -> None:
        self._drive(self.save_async(plugin_id, key, value, secret=secret))

    def delete(self, plugin_id: str, key: str) -> None:
        self._drive(self.delete_async(plugin_id, key))


__all__ = ["DbSettingsStore", "TABLE"]
