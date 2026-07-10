"""
@file_name: test_oauth_dual_slot.py
@date: 2026-07-07
@description: A subscription (OAuth) login binds BOTH the agent and helper_llm
slots to the subscription provider in one step, and set_slot now accepts an
OAuth provider in the helper_llm slot (served via the CLI helper).
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


class _FakeDB:
    def __init__(self):
        self.providers: dict[str, dict] = {}
        self.slots: dict[tuple, dict] = {}

    async def get(self, table, filters=None):
        filters = filters or {}
        rows = (
            list(self.providers.values()) if table == "user_providers"
            else list(self.slots.values()) if table == "user_slots"
            else []
        )
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
        for r in await self.get(table, filters):
            r.update(data)
        return 1

    async def delete(self, table, filters):
        return 0


@pytest.mark.asyncio
async def test_claude_oauth_add_binds_both_slots():
    db = _FakeDB()
    svc = UserProviderService(db)
    _config, new_ids = await svc.add_provider("u1", "claude_oauth")
    pid = new_ids[0]

    agent = db.slots.get(("u1", "agent"))
    helper = db.slots.get(("u1", "helper_llm"))
    assert agent and agent["provider_id"] == pid
    assert helper and helper["provider_id"] == pid
    # Framework auto-set to claude_code for the agent slot.
    assert agent["agent_framework"] == "claude_code"
    # Agent on the flagship, helper on the cheap model (opus / haiku split).
    assert agent["model"] == "opus"
    assert helper["model"] == "haiku"


@pytest.mark.asyncio
async def test_codex_oauth_add_binds_both_slots():
    db = _FakeDB()
    svc = UserProviderService(db)
    _config, new_ids = await svc.add_provider("u1", "codex_oauth")
    pid = new_ids[0]

    agent = db.slots.get(("u1", "agent"))
    helper = db.slots.get(("u1", "helper_llm"))
    assert agent and agent["provider_id"] == pid
    assert helper and helper["provider_id"] == pid
    assert agent["agent_framework"] == "codex_cli"
    # Agent on the flagship (curated[0]); helper on the cheap mini — NOT the
    # flagship (bug: previously both got curated[0] = gpt-5.5).
    assert agent["model"] == "gpt-5.5"
    assert helper["model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_set_slot_accepts_oauth_in_helper():
    """The old guard rejected OAuth in helper_llm; now it's allowed (CLI helper)."""
    db = _FakeDB()
    svc = UserProviderService(db)
    # Seed a claude_oauth provider WITHOUT going through add_provider's
    # auto-bind, so we exercise set_slot directly.
    db.providers["p_oauth"] = {
        "user_id": "u1", "provider_id": "p_oauth", "name": "Claude Code (OAuth)",
        "source": "claude_oauth", "protocol": "anthropic", "auth_type": "oauth",
        "api_key": "", "base_url": "", "models": json.dumps(["opus", "haiku"]),
    }
    # Should NOT raise (previously raised "helper_llm slot cannot use OAuth").
    await svc.set_slot("u1", "helper_llm", "p_oauth", "haiku")
    assert db.slots[("u1", "helper_llm")]["provider_id"] == "p_oauth"
