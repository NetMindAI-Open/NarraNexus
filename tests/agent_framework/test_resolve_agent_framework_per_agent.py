"""
@file_name: test_resolve_agent_framework_per_agent.py
@author: rujing.yan
@date: 2026-07-09
@description: step_3 agent-framework resolution is per-AGENT + owner-based.

``_resolve_agent_framework_name(agent_id, db)``:
  1. honours a per-agent override (agent_slots) that actually rebinds the
     agent slot (has a provider_id);
  2. otherwise falls back to the OWNER's user_slots default (NOT the trigger
     identity — the pre-fix bug);
  3. degrades to claude_code on missing rows / DB hiccup.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _resolve_agent_framework_name,
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
async def test_owner_default_when_no_override():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append(
        {"user_id": "u1", "slot_name": "agent", "agent_framework": "codex_cli"}
    )
    assert await _resolve_agent_framework_name("ag1", db) == "codex_cli"


@pytest.mark.asyncio
async def test_per_agent_override_wins():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append(
        {"user_id": "u1", "slot_name": "agent", "agent_framework": "claude_code"}
    )
    # Override rebinds the agent slot (has a provider) → its framework wins.
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent",
        "provider_id": "p_x", "agent_framework": "codex_cli",
    })
    assert await _resolve_agent_framework_name("ag1", db) == "codex_cli"


@pytest.mark.asyncio
async def test_framework_only_stub_does_not_win():
    """An override with no provider_id doesn't rebind the agent slot in the
    config resolver, so its framework must NOT win here either."""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append(
        {"user_id": "u1", "slot_name": "agent", "agent_framework": "claude_code"}
    )
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent",
        "provider_id": "", "agent_framework": "codex_cli",
    })
    assert await _resolve_agent_framework_name("ag1", db) == "claude_code"


@pytest.mark.asyncio
async def test_override_with_provider_but_null_framework_does_not_win():
    """Regression (PR #84): agent_slots.agent_framework is nullable. An
    override that has a provider_id but a NULL framework must NOT win — it
    falls through to the owner's user_slots framework. (Now enforced by the
    shared overlay in agent_model_identity that this function delegates to.)"""
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": "u1"})
    db.tables["user_slots"].append(
        {"user_id": "u1", "slot_name": "agent", "agent_framework": "codex_cli"}
    )
    db.tables["agent_slots"].append({
        "agent_id": "ag1", "slot_name": "agent",
        "provider_id": "p_x", "agent_framework": None,
    })
    assert await _resolve_agent_framework_name("ag1", db) == "codex_cli"


@pytest.mark.asyncio
async def test_missing_owner_defaults_to_claude_code():
    db = _FakeDB()
    db.tables["agents"].append({"agent_id": "ag1", "created_by": ""})
    assert await _resolve_agent_framework_name("ag1", db) == "claude_code"


@pytest.mark.asyncio
async def test_db_hiccup_defaults_to_claude_code():
    assert await _resolve_agent_framework_name("ag1", _BoomDB()) == "claude_code"
