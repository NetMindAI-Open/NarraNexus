"""
@file_name: test_m0002_workspace_nested_layout.py
@date: 2026-06-18
@description: Migration 0002 wires the flat→nested workspace move into the
versioned runner — it loads known users from the DB, migrates against
settings.base_working_path, is idempotent, and is registered in REGISTRY.
"""
from pathlib import Path

import pytest

from backend.migrations import REGISTRY
from backend.migrations.m0002_workspace_nested_layout import MIGRATION


def test_m0002_registered_and_ordered_after_m0001():
    ids = [m.id for m in REGISTRY]
    assert MIGRATION.id == "0002_workspace_nested_layout"
    assert MIGRATION in REGISTRY
    assert ids.index("0002_workspace_nested_layout") > ids.index("0001_unified_memory_backfill")


class _FakeDB:
    """Minimal stand-in — the migration only ever runs the users SELECT."""

    def __init__(self, user_ids):
        self._user_ids = user_ids

    async def execute(self, sql, params=(), fetch=False):
        if "users" in sql.lower():
            return [{"user_id": u} for u in self._user_ids]
        return []


def _make_flat(base: Path, name: str):
    d = base / name
    (d / "skills").mkdir(parents=True)
    (d / "marker.txt").write_text(name)


@pytest.mark.asyncio
async def test_m0002_moves_flat_to_nested_and_is_idempotent(tmp_path, monkeypatch):
    _make_flat(tmp_path, "agent_a1_userx")
    _make_flat(tmp_path, "agent_b2_usery")

    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))

    db = _FakeDB({"userx", "usery"})
    stats = await MIGRATION.apply(db)

    assert stats["moved"] == 2
    assert (tmp_path / "userx" / "agent_a1" / "marker.txt").read_text() == "agent_a1_userx"
    assert (tmp_path / "usery" / "agent_b2").is_dir()
    assert not (tmp_path / "agent_a1_userx").exists()

    # second run = no-op (already nested) — proves run-once safety under the runner
    stats2 = await MIGRATION.apply(db)
    assert stats2["moved"] == 0


@pytest.mark.asyncio
async def test_m0002_unknown_owner_left_in_place(tmp_path, monkeypatch):
    _make_flat(tmp_path, "agent_a1_stranger")  # 'stranger' not a known user

    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))

    db = _FakeDB({"userx"})
    stats = await MIGRATION.apply(db)

    assert stats["moved"] == 0
    assert (tmp_path / "agent_a1_stranger").is_dir()  # never guessed / moved


@pytest.mark.asyncio
async def test_m0002_tolerates_missing_users_table(tmp_path, monkeypatch):
    # A brand-new DB has no `users` table yet — the migration must NOT crash
    # the startup runner; it just migrates nothing.
    _make_flat(tmp_path, "agent_a1_userx")

    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))

    class _BadDB:
        async def execute(self, *a, **k):
            raise RuntimeError("no such table: users")

    stats = await MIGRATION.apply(_BadDB())  # must not raise
    assert stats["moved"] == 0
    assert (tmp_path / "agent_a1_userx").is_dir()  # left in place


@pytest.mark.asyncio
async def test_m0002_noop_when_base_dir_missing(tmp_path, monkeypatch):
    # Fresh install with no workspaces dir yet — early no-op, db untouched.
    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "does_not_exist"))

    stats = await MIGRATION.apply(object())  # db must never be touched
    assert stats["moved"] == 0
