"""
@file_name: test_cleanup_duplicate_pinned_artifacts.py
@author: Bin Liang
@date: 2026-07-23
@description: Tests for scripts/data_migrations/cleanup_duplicate_pinned_artifacts.py — the
one-shot cleanup that removes duplicate agent-scoped (pinned) artifact rows
pointing at the same entry file, keeping the newest row per group.

Covers:
- duplicate pinned rows (same agent_id + file_path) collapse to the newest row
- non-duplicate pinned rows and session-scoped rows are untouched
- dry run (apply=False) deletes nothing but reports the same plan
- re-running after apply is a no-op (idempotent)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "data_migrations"
    / "cleanup_duplicate_pinned_artifacts.py"
)
_spec = importlib.util.spec_from_file_location("cleanup_duplicate_pinned_artifacts", _SCRIPT)
cleanup_mod = importlib.util.module_from_spec(_spec)
sys.modules["cleanup_duplicate_pinned_artifacts"] = cleanup_mod
_spec.loader.exec_module(cleanup_mod)


async def _seed(db):
    rows = [
        # Three pinned rows for the same entry -> keep only the newest (art_c).
        ("art_a", "agent_1", None, 1, "u1/agent_1/welcome/index.html", "2026-06-01T00:00:00+00:00"),
        ("art_b", "agent_1", None, 1, "u1/agent_1/welcome/index.html", "2026-06-02T00:00:00+00:00"),
        ("art_c", "agent_1", None, 1, "u1/agent_1/welcome/index.html", "2026-06-03T00:00:00+00:00"),
        # Distinct pinned artifact on the same agent -> untouched.
        ("art_d", "agent_1", None, 1, "u1/agent_1/report/index.html", "2026-06-01T00:00:00+00:00"),
        # Session-scoped duplicates of the same file -> untouched (not pinned).
        ("art_e", "agent_2", "sess_1", 0, "u1/agent_2/doc/index.html", "2026-06-01T00:00:00+00:00"),
        ("art_f", "agent_2", "sess_1", 0, "u1/agent_2/doc/index.html", "2026-06-02T00:00:00+00:00"),
    ]
    for artifact_id, agent_id, session_id, pinned, file_path, created_at in rows:
        await db.insert("instance_artifacts", {
            "artifact_id": artifact_id,
            "agent_id": agent_id,
            "user_id": "u1",
            "session_id": session_id,
            "pinned": pinned,
            "title": "T",
            "kind": "text/html",
            "file_path": file_path,
            "size_bytes": 1,
            "created_at": created_at,
            "updated_at": created_at,
        })


async def _ids(db):
    rows = await db.execute(
        "SELECT artifact_id FROM instance_artifacts ORDER BY artifact_id",
        params=(), fetch=True,
    )
    return [r["artifact_id"] for r in rows]


@pytest.mark.asyncio
async def test_dry_run_reports_plan_but_deletes_nothing(db_client):
    await _seed(db_client)

    summary = await cleanup_mod.cleanup(db_client, apply=False)

    assert summary["groups"] == 1
    assert sorted(summary["to_delete"]) == ["art_a", "art_b"]
    assert await _ids(db_client) == ["art_a", "art_b", "art_c", "art_d", "art_e", "art_f"]


@pytest.mark.asyncio
async def test_apply_keeps_newest_and_is_idempotent(db_client):
    await _seed(db_client)

    summary = await cleanup_mod.cleanup(db_client, apply=True)
    assert sorted(summary["to_delete"]) == ["art_a", "art_b"]
    assert await _ids(db_client) == ["art_c", "art_d", "art_e", "art_f"]

    again = await cleanup_mod.cleanup(db_client, apply=True)
    assert again["groups"] == 0
    assert again["to_delete"] == []
    assert await _ids(db_client) == ["art_c", "art_d", "art_e", "art_f"]
