"""
@file_name: test_backfill_cost_records_user_id.py
@author: Bin Liang
@date: 2026-07-22
@description: Tests for scripts/backfill_cost_records_user_id.py — the SQL that
fills cost_records.user_id from agents.created_by. Runs against the in-memory
SQLite fixture.

Covers:
- a cost row whose agent still exists gets user_id = agents.created_by
- a cost row whose agent is gone (true orphan) stays NULL — never invented
- the count queries classify fillable vs orphan correctly
- re-running the backfill is idempotent (only NULL rows are touched)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "backfill_cost_records_user_id.py"
)
_spec = importlib.util.spec_from_file_location("backfill_cost_records_user_id", _SCRIPT)
bf = importlib.util.module_from_spec(_spec)
sys.modules["backfill_cost_records_user_id"] = bf
_spec.loader.exec_module(bf)


async def _seed(db):
    # Live agent owned by usr_live.
    await db.insert("agents", {
        "agent_id": "a_live",
        "agent_name": "Live",
        "created_by": "usr_live",
    })
    # Cost row attributable via the live agent (user_id omitted -> NULL).
    await db.insert("cost_records", {
        "agent_id": "a_live",
        "call_type": "agent_loop",
        "model": "claude-x",
    })
    # Cost row whose agent was hard-deleted -> true orphan.
    await db.insert("cost_records", {
        "agent_id": "a_dead",
        "call_type": "agent_loop",
        "model": "claude-x",
    })


async def _count(db, sql: str) -> int:
    rows = await db.execute(sql, params=(), fetch=True)
    return int(rows[0]["n"]) if rows else 0


@pytest.mark.asyncio
async def test_counts_classify_fillable_and_orphan(db_client):
    await _seed(db_client)
    assert await _count(db_client, bf._COUNT_FILLABLE) == 1
    assert await _count(db_client, bf._COUNT_ORPHAN) == 1


@pytest.mark.asyncio
async def test_backfill_fills_live_agent_and_leaves_orphan_null(db_client):
    await _seed(db_client)
    await db_client.execute(bf._BACKFILL, params=(), fetch=False)

    live = await db_client.execute(
        "SELECT user_id FROM cost_records WHERE agent_id = %s",
        params=("a_live",),
        fetch=True,
    )
    dead = await db_client.execute(
        "SELECT user_id FROM cost_records WHERE agent_id = %s",
        params=("a_dead",),
        fetch=True,
    )
    assert live[0]["user_id"] == "usr_live"
    assert dead[0]["user_id"] is None


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db_client):
    await _seed(db_client)
    await db_client.execute(bf._BACKFILL, params=(), fetch=False)
    # Second run must not error and must not change the orphan count.
    await db_client.execute(bf._BACKFILL, params=(), fetch=False)
    assert await _count(db_client, bf._COUNT_ORPHAN) == 1
    assert await _count(db_client, bf._COUNT_FILLABLE) == 0
