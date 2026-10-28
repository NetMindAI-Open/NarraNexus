"""
@file_name: test_claude_oauth_model_list.py
@date: 2026-07-30
@description: The claude_oauth card's model list is read-time overridden with
the CLI family aliases (opus/sonnet/haiku) — mirror of the codex_oauth
curated-list override. Legacy cards created before the alias switch stored
pinned full ids; once those ids died upstream they kept looking valid to the
slot self-heal (membership test against the stored column), so agents ran a
dead model forever.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.agent_framework.providers.user_service import UserProviderService


class _FakeDB:
    def __init__(self):
        self.providers: dict[str, dict] = {}
        # Every other table is kept too: add_provider writes the agent
        # framework to user_slots BEFORE binding the slot, and set_slot's
        # protocol check reads it back — a fake that drops the write would
        # validate against the default framework instead.
        self.tables: dict[str, list[dict]] = {}

    async def get(self, table, filters=None):
        filters = filters or {}
        rows = self.providers.values() if table == "user_providers" else self.tables.get(table, [])
        return [r for r in rows if all(r.get(k) == v for k, v in filters.items())]

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        if table == "user_providers":
            self.providers[data["provider_id"]] = dict(data)
        else:
            self.tables.setdefault(table, []).append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        return 0


@pytest.mark.asyncio
async def test_legacy_pinned_models_column_is_overridden_with_aliases():
    db = _FakeDB()
    db.providers["prov_old"] = {
        "provider_id": "prov_old", "user_id": "u1",
        "name": "Claude Code (OAuth)", "source": "claude_oauth",
        "protocol": "anthropic", "auth_type": "oauth", "api_key": "",
        "base_url": "",
        # Pre-alias era card: pinned full ids that have since died upstream.
        "models": json.dumps(["claude-opus-4-1", "claude-sonnet-4-5"]),
        "is_active": 1,
    }
    svc = UserProviderService(db)
    config = await svc.get_user_config("u1")
    assert config.providers["prov_old"].models == ["opus", "sonnet", "haiku"]


@pytest.mark.asyncio
async def test_fresh_claude_oauth_card_reads_aliases_too():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(user_id="u1", card_type="claude_oauth")
    config = await svc.get_user_config("u1")
    assert config.providers[new_ids[0]].models == ["opus", "sonnet", "haiku"]
