"""
@file_name: test_agent_model_identity.py
@author:
@date: 2026-07-10
@description: resolve_agent_model_identity resolves an agent's REAL
(framework, model) from the same slot overlay the runtime dispatches on.

Mirrors the overlay contract of ``_resolve_agent_framework_name``:
  1. per-agent agent_slots override wins ONLY with a provider_id;
  2. else the OWNER's user_slots default (keyed by agents.created_by);
  3. degrades to (claude_code, "") on missing rows / DB hiccup.

Both framework AND model come from the winning slot row, and the
framework maps to a human display label.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_framework.agent_model_identity import (
    FRAMEWORK_DISPLAY_NAMES,
    resolve_agent_model_identity,
)


class _FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get_one(self, table, filters):
        for r in self.tables[table]:
            if all(r.get(k) == v for k, v in filters.items()):
                return r
        return None


class _BoomDB:
    async def get_one(self, table, filters):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_owner_default_framework_and_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.framework_display == "Codex CLI"
    assert ident.model == "gpt-5"


@pytest.mark.asyncio
async def test_per_agent_override_wins_framework_and_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "claude_code", "model": "claude-sonnet-4",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "p_x",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.model == "gpt-5"


@pytest.mark.asyncio
async def test_framework_only_stub_does_not_win():
    """An override with no provider_id doesn't rebind the slot, so the
    owner's user_slots framework + model win (same as the dispatcher)."""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "claude_code", "model": "claude-sonnet-4",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "claude_code"
    assert ident.framework_display == "Claude Agent SDK"
    assert ident.model == "claude-sonnet-4"


@pytest.mark.asyncio
async def test_override_with_provider_but_null_framework_does_not_win():
    """Regression (PR #84 review): agent_slots.agent_framework is nullable.
    An override with a provider_id but NULL agent_framework must NOT win —
    otherwise the driver runs the owner's framework (codex_cli) while this
    resolver would render the override's empty framework as Claude. Must
    fall through to the owner's user_slots, matching the dispatch-side
    ``_resolve_agent_framework_name``."""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "codex_cli", "model": "gpt-5",
    })
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent", "provider_id": "p_x",
        "agent_framework": None, "model": "gpt-5-override",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "codex_cli"
    assert ident.framework_display == "Codex CLI"


@pytest.mark.asyncio
async def test_missing_rows_default_to_claude_code_empty_model():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": ""})
    ident = await resolve_agent_model_identity("ag1", db)
    assert ident.framework == "claude_code"
    assert ident.framework_display == "Claude Agent SDK"
    assert ident.model == ""


@pytest.mark.asyncio
async def test_db_hiccup_degrades_gracefully():
    ident = await resolve_agent_model_identity("ag1", _BoomDB())
    assert ident.framework == "claude_code"
    assert ident.model == ""


@pytest.mark.asyncio
async def test_unknown_framework_falls_back_to_raw_name():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append({
        "user_id": "u1", "slot_name": "agent",
        "agent_framework": "some_future_cli", "model": "m1",
    })
    ident = await resolve_agent_model_identity("ag1", db)
    # Unknown canonical name is shown verbatim — never invent a brand.
    assert "some_future_cli" not in FRAMEWORK_DISPLAY_NAMES
    assert ident.framework_display == "some_future_cli"
