"""
@file_name: test_user_provider_service_agent_framework.py
@date: 2026-05-29
@description: Tests for UserProviderService.get_user_agent_framework /
set_user_agent_framework — the upsert semantics + validation.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_framework.user_provider_service import UserProviderService


class _MemoryDB:
    """Tiny in-memory user_slots stand-in keyed by (user_id, slot_name)."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    async def get_one(self, table, filters):
        if table != "user_slots":
            return None
        key = (filters["user_id"], filters["slot_name"])
        return dict(self.rows.get(key, {})) if key in self.rows else None

    async def insert(self, table, data):
        key = (data["user_id"], data["slot_name"])
        self.rows[key] = dict(data)

    async def update(self, table, filters, data):
        key = (filters["user_id"], filters["slot_name"])
        if key in self.rows:
            self.rows[key].update(data)


# ---------------- get_user_agent_framework ------------------------


@pytest.mark.asyncio
async def test_get_returns_claude_code_default_when_no_row():
    svc = UserProviderService(_MemoryDB())
    assert await svc.get_user_agent_framework("new_user") == "claude_code"


@pytest.mark.asyncio
async def test_get_returns_claude_code_when_column_null():
    db = _MemoryDB()
    db.rows[("u1", "agent")] = {
        "user_id": "u1",
        "slot_name": "agent",
        "agent_framework": None,
        "provider_id": "p1",
        "model": "claude-sonnet-4-5",
    }
    svc = UserProviderService(db)
    assert await svc.get_user_agent_framework("u1") == "claude_code"


@pytest.mark.asyncio
async def test_get_returns_stored_codex_cli():
    db = _MemoryDB()
    db.rows[("u1", "agent")] = {
        "user_id": "u1",
        "slot_name": "agent",
        "agent_framework": "codex_cli",
        "provider_id": "",
        "model": "",
    }
    svc = UserProviderService(db)
    assert await svc.get_user_agent_framework("u1") == "codex_cli"


# ---------------- set_user_agent_framework ------------------------


@pytest.mark.asyncio
async def test_set_inserts_stub_row_for_new_user():
    db = _MemoryDB()
    svc = UserProviderService(db)
    await svc.set_user_agent_framework("u_new", "codex_cli")
    row = db.rows[("u_new", "agent")]
    assert row["agent_framework"] == "codex_cli"
    assert row["provider_id"] == ""
    assert row["model"] == ""


@pytest.mark.asyncio
async def test_set_updates_existing_row():
    db = _MemoryDB()
    db.rows[("u1", "agent")] = {
        "user_id": "u1",
        "slot_name": "agent",
        "agent_framework": "claude_code",
        "provider_id": "p1",
        "model": "claude-sonnet-4-5",
    }
    svc = UserProviderService(db)
    await svc.set_user_agent_framework("u1", "codex_cli")
    row = db.rows[("u1", "agent")]
    assert row["agent_framework"] == "codex_cli"
    # Existing provider_id + model preserved
    assert row["provider_id"] == "p1"
    assert row["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_set_rejects_unknown_framework():
    svc = UserProviderService(_MemoryDB())
    with pytest.raises(ValueError, match="Unknown agent_framework"):
        await svc.set_user_agent_framework("u1", "bogus_framework")


@pytest.mark.asyncio
async def test_set_then_get_round_trip():
    db = _MemoryDB()
    svc = UserProviderService(db)
    await svc.set_user_agent_framework("u1", "codex_cli")
    assert await svc.get_user_agent_framework("u1") == "codex_cli"
    await svc.set_user_agent_framework("u1", "claude_code")
    assert await svc.get_user_agent_framework("u1") == "claude_code"


@pytest.mark.asyncio
async def test_set_accepts_both_supported_frameworks():
    svc = UserProviderService(_MemoryDB())
    for fw in ("claude_code", "codex_cli"):
        await svc.set_user_agent_framework(f"u_{fw}", fw)
        assert await svc.get_user_agent_framework(f"u_{fw}") == fw
