"""
@file_name: test_provider_replace.py
@date: 2026-07-07
@description: Tests for the provider quick-add REPLACE flow.

Rotating a NetMind key used to hit "A netmind provider already exists" because
add_provider guards aggregator card types by `source`. The replace flow lets
onboard_one_key either (a) report needs_replace so the UI can confirm, or
(b) atomically swap the old dual-provider pair for a new one via
expand-contract (add new -> repoint slots -> delete old) so the user is never
left without a working provider.
"""
from __future__ import annotations

import contextvars

import pytest

from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


class _FakeDB:
    """In-memory user_providers / user_slots stand-in WITH working delete."""

    def __init__(self):
        self.providers: dict[str, dict] = {}
        self.slots: dict[tuple, dict] = {}

    async def get(self, table, filters=None):
        filters = filters or {}
        if table == "user_providers":
            rows = list(self.providers.values())
        elif table == "user_slots":
            rows = list(self.slots.values())
        else:
            return []
        return [r for r in rows if all(r.get(k) == v for k, v in filters.items())]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        if table == "user_providers":
            self.providers[data["provider_id"]] = dict(data)
        elif table == "user_slots":
            self.slots[(data["user_id"], data["slot_name"])] = dict(data)

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        if table == "user_providers":
            victims = [
                pid for pid, r in self.providers.items()
                if all(r.get(k) == v for k, v in filters.items())
            ]
            for pid in victims:
                del self.providers[pid]
            return len(victims)
        if table == "user_slots":
            victims = [
                k for k, r in self.slots.items()
                if all(r.get(fk) == fv for fk, fv in filters.items())
            ]
            for k in victims:
                del self.slots[k]
            return len(victims)
        return 0


@pytest.fixture(autouse=True)
def _stub_key_probe(monkeypatch):
    from xyz_agent_context.agent_framework.provider_registry import provider_registry

    async def _ok(provider):
        return True, "Connected successfully"

    monkeypatch.setattr(provider_registry, "test_provider", _ok)


def _run_isolated(coro_fn):
    """onboard_one_key hot-reloads via ContextVars; isolate per test."""
    ctx = contextvars.copy_context()
    return ctx.run(coro_fn)


async def _onboard(db, user_id, key, ptype, replace=False):
    svc = UserProviderService(db)
    return await svc.onboard_one_key(user_id, key, ptype, replace=replace)


@pytest.mark.asyncio
async def test_first_onboard_creates_two_netmind_providers():
    db = _FakeDB()
    _config, new_ids, meta = await _onboard(db, "u1", "nm-key-1", "netmind")
    assert len(new_ids) == 2
    assert not meta.get("needs_replace")
    assert len(await db.get("user_providers", {"user_id": "u1", "source": "netmind"})) == 2


@pytest.mark.asyncio
async def test_second_onboard_without_replace_signals_needs_replace():
    db = _FakeDB()
    await _onboard(db, "u1", "nm-key-1", "netmind")
    before = dict(db.providers)

    _config, new_ids, meta = await _onboard(db, "u1", "nm-key-2", "netmind")

    assert meta.get("needs_replace") is True
    assert meta.get("provider_type") == "netmind"
    # Masked tail of the CURRENT key, so the prompt can name it.
    assert meta.get("existing_masked", "").endswith("ey-1")
    assert new_ids == []
    # Nothing mutated — no duplicate rows, old pair intact.
    assert db.providers == before


@pytest.mark.asyncio
async def test_replace_swaps_pair_atomically_and_repoints_slots():
    db = _FakeDB()
    await _onboard(db, "u1", "nm-key-1", "netmind")
    old_ids = set(db.providers.keys())

    _config, new_ids, meta = await _onboard(
        db, "u1", "nm-key-2", "netmind", replace=True
    )

    assert not meta.get("needs_replace")
    # Exactly the new pair remains; every old row gone.
    remaining = set(db.providers.keys())
    assert remaining == set(new_ids)
    assert remaining.isdisjoint(old_ids)
    assert len(await db.get("user_providers", {"user_id": "u1", "source": "netmind"})) == 2

    # Both slots point at surviving providers (no dangling references).
    slot_pids = {s["provider_id"] for s in db.slots.values()}
    assert slot_pids <= remaining
    # The new key is what got stored.
    for r in db.providers.values():
        assert r["api_key"] == "nm-key-2"


@pytest.mark.asyncio
async def test_add_provider_replace_flag_skips_uniqueness_guard():
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.add_provider("u1", "netmind", api_key="nm-key-1")
    # Without replace -> guarded.
    with pytest.raises(ValueError, match="already exists"):
        await svc.add_provider("u1", "netmind", api_key="nm-key-2")
    # With replace -> allowed (inserts a fresh pair alongside).
    _cfg, ids = await svc.add_provider("u1", "netmind", api_key="nm-key-2", replace=True)
    assert len(ids) == 2
