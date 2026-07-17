"""
@file_name: test_migrate_users_to_netmind.py
@author: NarraNexus
@date: 2026-06-11
@description: Tests for the one-shot legacy-user -> NetMind-id migration
(scripts/migrate_users_to_netmind.py). Runs against the in-memory SQLite
fixture; the real run targets MySQL offline (stack stopped) — NEVER via
backend lifespan (v1.7.16 lesson).

Covers:
- column classification: every identity-named column in schema_registry is
  explicitly classified (include / exclude / conditional) — drift fails loudly
- report: old_user_id -> email resolution via invite_codes; missing email
  flagged for manual handling
- execute: rewrites every included column, leaves excluded columns (IM uids,
  bus_channels.created_by=agent id) and non-user memory scopes alone,
  renames workspace dirs, stamps users.metadata
- verify: residual count of old ids is zero after execute
- idempotency: a second execute is a no-op
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "migrate_users_to_netmind.py"
)
_spec = importlib.util.spec_from_file_location("migrate_users_to_netmind", _SCRIPT)
mig = importlib.util.module_from_spec(_spec)
sys.modules["migrate_users_to_netmind"] = mig
_spec.loader.exec_module(mig)


OLD = "binliang"
NEW = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


def test_every_identity_column_is_classified():
    cols = mig.classify_identity_columns()

    plain = set(cols.plain)
    assert ("agents", "created_by") in plain
    assert ("users", "user_id") in plain
    assert ("user_providers", "owner_user_id") in plain
    assert ("invite_codes", "used_by_user_id") in plain

    # IM-platform uids and agent-id columns must NOT be rewritten
    assert ("channel_slack_credentials", "owner_user_id") not in plain
    assert ("channel_telegram_credentials", "owner_user_id") not in plain
    assert ("bus_channels", "created_by") not in plain

    # memory_* scope_id is conditional on scope_type='user'
    assert "memory_event" in cols.memory_scope_tables


@pytest.mark.asyncio
async def test_report_resolves_email_via_invite_codes(db_client):
    await db_client.insert("users", {"user_id": OLD, "user_type": "individual"})
    await db_client.insert("users", {"user_id": "noemail", "user_type": "individual"})
    await db_client.insert(
        "invite_codes",
        {"code": "NX-TEST1", "email": "bin@x.com", "status": "used",
         "used_by_user_id": OLD},
    )

    rows = await mig.build_report(db_client)
    by_id = {r["user_id"]: r for r in rows}

    assert by_id[OLD]["email"] == "bin@x.com"
    assert by_id[OLD]["issue"] is None
    assert by_id["noemail"]["email"] is None
    assert by_id["noemail"]["issue"] == "no_email"


async def _seed_world(db_client, tmp_path):
    await db_client.insert("users", {"user_id": OLD, "user_type": "individual"})
    await db_client.insert(
        "agents",
        {"agent_id": "agent_abc123", "agent_name": "A", "created_by": OLD},
    )
    await db_client.insert(
        "user_quotas",
        {"user_id": OLD, "initial_input_tokens": 1, "initial_output_tokens": 1,
         "used_input_tokens": 0, "used_output_tokens": 0,
         "granted_input_tokens": 0, "granted_output_tokens": 0},
    )
    # memory rows: user-scoped must move, agent-scoped must not
    await db_client.insert(
        "memory_event",
        {"record_id": "m1", "agent_id": "agent_abc123", "scope_type": "user",
         "scope_id": OLD, "kind": "event", "content_text": "x"},
    )
    await db_client.insert(
        "memory_event",
        {"record_id": "m2", "agent_id": "agent_abc123", "scope_type": "agent",
         "scope_id": OLD, "kind": "event", "content_text": "x"},
    )
    # excluded: slack credential owner is a SLACK uid that happens to collide
    await db_client.insert(
        "channel_slack_credentials",
        {"agent_id": "agent_abc123", "owner_user_id": OLD,
         "bot_token_encoded": "t", "app_token_encoded": "t"},
    )
    ws = tmp_path / agent_workspace_relpath("agent_abc123", OLD)
    ws.mkdir(parents=True)
    (ws / "Bootstrap.md").write_text("hi")
    return ws


@pytest.mark.asyncio
async def test_execute_rewrites_renames_and_stamps(db_client, tmp_path):
    await _seed_world(db_client, tmp_path)

    stats = await mig.execute_migration(
        db_client, {OLD: NEW}, base_working_path=str(tmp_path)
    )

    assert (await db_client.get_one("users", {"user_id": NEW})) is not None
    assert (await db_client.get_one("users", {"user_id": OLD})) is None
    assert (await db_client.get_one("agents", {"agent_id": "agent_abc123"}))[
        "created_by"
    ] == NEW
    assert (await db_client.get_one("user_quotas", {"user_id": NEW})) is not None

    m1 = await db_client.get_one("memory_event", {"record_id": "m1"})
    m2 = await db_client.get_one("memory_event", {"record_id": "m2"})
    assert m1["scope_id"] == NEW          # user scope migrated
    assert m2["scope_id"] == OLD          # agent scope untouched

    slack = await db_client.get_one(
        "channel_slack_credentials", {"agent_id": "agent_abc123"}
    )
    assert slack["owner_user_id"] == OLD  # IM uid untouched

    assert not (tmp_path / agent_workspace_relpath("agent_abc123", OLD)).exists()
    assert (tmp_path / agent_workspace_relpath("agent_abc123", NEW) / "Bootstrap.md").exists()

    user = await db_client.get_one("users", {"user_id": NEW})
    assert OLD in (user.get("metadata") or "")  # migration stamp keeps old id

    assert stats["users_migrated"] == 1


@pytest.mark.asyncio
async def test_verify_counts_residuals(db_client, tmp_path):
    await _seed_world(db_client, tmp_path)

    before = await mig.verify_migration(db_client, [OLD])
    assert sum(before.values()) > 0

    await mig.execute_migration(db_client, {OLD: NEW}, base_working_path=str(tmp_path))

    after = await mig.verify_migration(db_client, [OLD])
    assert sum(after.values()) == 0


@pytest.mark.asyncio
async def test_execute_is_idempotent(db_client, tmp_path):
    await _seed_world(db_client, tmp_path)
    await mig.execute_migration(db_client, {OLD: NEW}, base_working_path=str(tmp_path))

    stats2 = await mig.execute_migration(
        db_client, {OLD: NEW}, base_working_path=str(tmp_path)
    )

    assert stats2["users_migrated"] == 0  # nothing left to do
    assert (tmp_path / agent_workspace_relpath("agent_abc123", NEW)).exists()
