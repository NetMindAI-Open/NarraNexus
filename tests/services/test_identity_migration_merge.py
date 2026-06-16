"""
@file_name: test_identity_migration_merge.py
@author: NarraNexus
@date: 2026-06-12
@description: MERGE path of the identity migration — when the target
userSystemCode row ALREADY EXISTS (the user logged in via NetMind before we
rekeyed their legacy data), the rewrite must merge legacy data into the
existing target row instead of renaming the legacy row onto it (which would
collide on the unique user_id / per-user-config indexes).

Rule:
- multi-row business data (agents, memory user-scope) → moves to the target
- single-row per-user config (user_quotas, user_settings) and the users row
  itself → keep the EXISTING target row, drop the legacy duplicate
"""
from __future__ import annotations

import pytest

from xyz_agent_context.services import identity_migration as mig

OLD = "binliang"
HEX = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def _quota(user_id: str, initial: int):
    return {
        "user_id": user_id,
        "initial_input_tokens": initial, "initial_output_tokens": initial,
        "used_input_tokens": 0, "used_output_tokens": 0,
        "granted_input_tokens": 0, "granted_output_tokens": 0,
    }


async def _seed_merge_world(db_client):
    # Legacy user + their data.
    await db_client.insert("users", {"user_id": OLD, "user_type": "individual"})
    await db_client.insert(
        "agents",
        {"agent_id": "agent_old1", "agent_name": "A", "created_by": OLD},
    )
    await db_client.insert("user_quotas", _quota(OLD, 111))
    await db_client.insert(
        "memory_event",
        {"record_id": "m1", "agent_id": "agent_old1", "scope_type": "user",
         "scope_id": OLD, "kind": "event", "content_text": "x"},
    )
    # TARGET hex row ALREADY EXISTS — the user logged in via NetMind first,
    # so users + a per-user config row are already keyed by the hex.
    await db_client.insert("users", {"user_id": HEX, "user_type": "individual"})
    await db_client.insert("user_quotas", _quota(HEX, 999))


@pytest.mark.asyncio
async def test_merge_moves_business_data_keeps_target_config(db_client, tmp_path):
    await _seed_merge_world(db_client)

    stats = await mig.execute_migration(
        db_client, {OLD: HEX}, base_working_path=str(tmp_path)
    )

    # Multi-row business data moved onto the target.
    assert (await db_client.get_one("agents", {"agent_id": "agent_old1"}))[
        "created_by"
    ] == HEX
    assert (await db_client.get_one("memory_event", {"record_id": "m1"}))[
        "scope_id"
    ] == HEX

    # Legacy users row dropped; target row kept.
    assert (await db_client.get_one("users", {"user_id": OLD})) is None
    assert (await db_client.get_one("users", {"user_id": HEX})) is not None

    # Single-row config: exactly the TARGET's quota row survives (initial=999),
    # the legacy duplicate (initial=111) is dropped — no unique-index collision.
    quotas = await db_client.execute(
        "SELECT user_id, initial_input_tokens AS n FROM user_quotas",
        fetch=True,
    )
    assert [(q["user_id"], q["n"]) for q in quotas] == [(HEX, 999)]

    assert stats["users_merged"] == 1
    assert stats["users_migrated"] == 1


@pytest.mark.asyncio
async def test_no_residual_old_id_after_merge(db_client, tmp_path):
    await _seed_merge_world(db_client)
    await mig.execute_migration(db_client, {OLD: HEX}, base_working_path=str(tmp_path))

    residuals = await mig.verify_migration(db_client, [OLD])
    assert residuals[OLD] == 0
