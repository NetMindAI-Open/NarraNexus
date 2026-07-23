"""
@file_name: test_m0003_cloud_codex_to_claude.py
@author:
@date: 2026-07-16
@description: Migration 0003 — cloud-only flip of codex_cli agent framework to
claude_code. Verifies cloud gate, agent-only scope, idempotence.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.migrations import REGISTRY
from xyz_agent_context.migrations.m0003_cloud_codex_to_claude import MIGRATION
from xyz_agent_context.utils.deployment_mode import DEPLOYMENT_MODE_ENV_VAR


def test_registered():
    assert MIGRATION.id == "0003_cloud_codex_to_claude"
    assert MIGRATION in REGISTRY


async def _seed_slot(db, user_id, slot_name, framework):
    await db.insert("user_slots", {
        "user_id": user_id,
        "slot_name": slot_name,
        "provider_id": f"pid_{user_id}",
        "model": "m",
        "agent_framework": framework,
    })


async def _seed_agent_slot(db, agent_id, framework):
    await db.insert("agent_slots", {
        "agent_id": agent_id,
        "slot_name": "agent",
        "provider_id": f"pid_{agent_id}",
        "model": "m",
        "agent_framework": framework,
    })


async def _fw(db, user_id, slot_name="agent"):
    row = await db.get_one("user_slots", {"user_id": user_id, "slot_name": slot_name})
    return row["agent_framework"]


async def _agent_fw(db, agent_id):
    row = await db.get_one("agent_slots", {"agent_id": agent_id, "slot_name": "agent"})
    return row["agent_framework"]


@pytest.mark.asyncio
async def test_cloud_flips_codex_agent_only_and_idempotent(db_client, monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "cloud")
    await _seed_slot(db_client, "u_codex", "agent", "codex_cli")
    await _seed_slot(db_client, "u_claude", "agent", "claude_code")
    await _seed_slot(db_client, "u_codex", "helper_llm", "codex_cli")  # must NOT flip
    # per-agent overrides: one codex (must flip), one already claude (unchanged)
    await _seed_agent_slot(db_client, "ag_codex", "codex_cli")
    await _seed_agent_slot(db_client, "ag_claude", "claude_code")

    stats = await MIGRATION.apply(db_client)
    assert stats == {"migrated_user_slots": 1, "migrated_agent_slots": 1}
    assert await _fw(db_client, "u_codex", "agent") == "claude_code"      # flipped
    assert await _fw(db_client, "u_claude", "agent") == "claude_code"     # unchanged
    assert await _fw(db_client, "u_codex", "helper_llm") == "codex_cli"   # helper untouched
    assert await _agent_fw(db_client, "ag_codex") == "claude_code"        # per-agent flipped
    assert await _agent_fw(db_client, "ag_claude") == "claude_code"       # unchanged

    # Idempotent — a re-run finds no codex_cli rows in either table.
    stats2 = await MIGRATION.apply(db_client)
    assert stats2 == {"migrated_user_slots": 0, "migrated_agent_slots": 0}


@pytest.mark.asyncio
async def test_local_mode_is_skipped(db_client, monkeypatch):
    monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, "local")
    await _seed_slot(db_client, "u_local", "agent", "codex_cli")
    await _seed_agent_slot(db_client, "ag_local", "codex_cli")

    stats = await MIGRATION.apply(db_client)
    assert stats == {"skipped": "local mode"}
    assert await _fw(db_client, "u_local", "agent") == "codex_cli"    # local codex left alone
    assert await _agent_fw(db_client, "ag_local") == "codex_cli"
