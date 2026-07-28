"""
@file_name: test_agent_field_length.py
@author: NetMind.AI
@date: 2026-07-23
@description: Import-time clamping of over-long agent_name / agent_description.

Regression for NetMindAI-Open/NarraNexus#71: a .nxbundle can carry an
agent_description (or agent_name) longer than AGENT_TEXT_MAX_LENGTH. The raw
db.insert on the import path bypasses the Agent model's length validation, so
the over-long value used to land in the DB and then every later edit/delete
(which deserializes the row through the Agent model) failed with Pydantic
string_too_long. The importer now trims to the ceiling and reports each
trimmed agent in the import summary.
"""

import pytest

from xyz_agent_context.schema.entity_schema import AGENT_TEXT_MAX_LENGTH

# Fixtures mirror tests/bundle/test_roundtrip.py (per-file convention there).


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")
    from xyz_agent_context.utils.db import db_factory
    db_factory._clients_by_loop.clear()
    from xyz_agent_context.utils.db.db_factory import get_db_client
    from xyz_agent_context.utils.db.schema_registry import auto_migrate
    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_overlong_agent(db, agent_id, user_id, name, description):
    """Raw-insert an agent whose name/description may exceed the ceiling —
    exactly how a poisoned row (or a foreign bundle's row) looks. Raw insert
    bypasses the Agent model, same as the import path does."""
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert("users", {
            "user_id": user_id, "user_type": "local", "role": "user",
            "display_name": "Seed User",
        })
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": name, "created_by": user_id,
        "agent_description": description, "agent_type": "default",
    })


async def _export_then_import(db, tmp_workspace_root, agent_id, owner_id, importer_id):
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    ws = tmp_workspace_root / f"{agent_id}_{owner_id}"
    ws.mkdir()
    (ws / "notes.md").write_text("workspace notes\n", encoding="utf-8")

    bundle_path = tmp_workspace_root.parent / "over255.nxbundle"
    await build_bundle(owner_id, ExportSelection(agent_ids=[agent_id]), bundle_path)

    pre = await preflight(bundle_path, importer_id)
    summary = await confirm(pre["preflight_token"], importer_id)
    rows = await db.get("agents", {"created_by": importer_id})
    return summary, rows


@pytest.mark.asyncio
async def test_import_trims_overlong_description(db_client, tmp_workspace_root):
    long_desc = "D" * 400  # well over the 255 ceiling
    await _seed_overlong_agent(
        db_client, "agent_over0001desc", "owner", "Normal Name", long_desc
    )
    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_over0001desc", "owner", "importer"
    )
    assert len(rows) == 1
    imported = rows[0]
    # Description clamped exactly to the ceiling; the imported row must be
    # readable back through the Agent model (this is what #71 broke).
    assert len(imported["agent_description"]) == AGENT_TEXT_MAX_LENGTH
    assert imported["agent_description"] == "D" * AGENT_TEXT_MAX_LENGTH

    from xyz_agent_context.repository.agent_repository import AgentRepository
    agent = await AgentRepository(db_client).get_agent(imported["agent_id"])
    assert agent is not None  # no string_too_long — the whole point

    # Summary flags exactly this agent + field.
    trimmed = summary["agent_fields_trimmed"]
    assert len(trimmed) == 1
    assert trimmed[0]["fields"] == ["agent_description"]
    assert any("trimmed" in w for w in summary["warnings"])


@pytest.mark.asyncio
async def test_import_trims_overlong_name(db_client, tmp_workspace_root):
    long_name = "N" * 300
    await _seed_overlong_agent(
        db_client, "agent_over0002name", "owner", long_name, "short desc"
    )
    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_over0002name", "owner", "importer2"
    )
    assert len(rows) == 1
    assert len(rows[0]["agent_name"]) <= AGENT_TEXT_MAX_LENGTH
    trimmed = summary["agent_fields_trimmed"]
    assert len(trimmed) == 1
    assert "agent_name" in trimmed[0]["fields"]


@pytest.mark.asyncio
async def test_dedupe_suffix_stays_within_limit_on_repeat_import(db_client, tmp_workspace_root):
    """Importing the same over-long-named bundle to the same user twice: the
    2nd import clamps to 255, clashes with the 1st, and dedupe_name appends a
    ' (1)' suffix. That suffix must NOT push the stored name back over the
    ceiling — otherwise the row is unreadable again (the #71 bug, re-opened by
    clamping before dedupe). Regression for review finding #1."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm
    from xyz_agent_context.repository.agent_repository import AgentRepository

    await _seed_overlong_agent(db_client, "agent_dup0001x", "owner", "N" * 300, "desc")
    ws = tmp_workspace_root / "agent_dup0001x_owner"
    ws.mkdir()
    (ws / "notes.md").write_text("x\n", encoding="utf-8")
    bundle = tmp_workspace_root.parent / "dup.nxbundle"
    await build_bundle("owner", ExportSelection(agent_ids=["agent_dup0001x"]), bundle)

    importer = "dupimporter"
    for _ in range(2):
        pre = await preflight(bundle, importer)
        await confirm(pre["preflight_token"], importer)

    rows = await db_client.get("agents", {"created_by": importer})
    assert len(rows) == 2, "both imports should create an agent"
    repo = AgentRepository(db_client)
    for r in rows:
        assert len(r["agent_name"]) <= AGENT_TEXT_MAX_LENGTH
        # Must read back through the Agent model without string_too_long.
        assert await repo.get_agent(r["agent_id"]) is not None


@pytest.mark.asyncio
async def test_import_within_limit_no_trim(db_client, tmp_workspace_root):
    """A normal-length agent imports with no trim warning."""
    await _seed_overlong_agent(
        db_client, "agent_ok0003fine", "owner", "Fine Name", "A fine description"
    )
    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ok0003fine", "owner", "importer3"
    )
    assert len(rows) == 1
    assert summary["agent_fields_trimmed"] == []
