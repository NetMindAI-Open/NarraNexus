"""
@file_name: test_roundtrip.py
@author: NetMind.AI
@date: 2026-05-08
@description: ID Rewrite Layer 5 — bundle export/import roundtrip integration test

Asserts:
1. After export → import, every imported row's *_id columns are valid
   (regex-match a known ID kind from id_schema.ID_KINDS).
2. No row references an agent_id outside the imported set (no dangling FKs).
3. ID rewrite occurred — imported agent_ids do NOT equal the original
   (every old ID got remapped).
4. Free-text fields (event_log etc.) had any ID strings rewritten too
   (Layer 4 sanity).
5. Name suffix applied when there's a clash with an existing agent.

Runs against an isolated SQLite file (not the user's nexus.db) so it's
safe to run repeatedly. Each test sets up its own data, runs export +
import in sequence, asserts, and tears down.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from xyz_agent_context.bundle.id_schema import ID_KINDS, build_all_id_regex


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    """Override settings.base_working_path + Path.home() so the test never
    touches the real ~/.nexusagent or repo working paths."""
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    # `Path.home()` reads HOME at call time on POSIX; envvar swap is enough.
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    """Create a fresh sqlite-backed AsyncDatabaseClient for this test.

    Patches `settings.database_url` directly (BaseSettings caches env at
    import time, so setenv alone is not enough) and clears the per-loop
    client cache so we get a brand-new client tied to this test's DB.
    """
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")

    from xyz_agent_context.utils import db_factory
    db_factory._clients_by_loop.clear()

    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id: str, agent_name: str, user_id: str = "test_user"):
    """Create a minimal agent + 1 narrative + 2 events + 1 module instance + 1 social entity.
    All child IDs are deterministically derived from `agent_id` so multiple seeded
    agents in the same DB don't collide on UNIQUE constraints."""
    existing_user = await db.get_one("users", {"user_id": user_id})
    if not existing_user:
        await db.insert("users", {
            "user_id": user_id,
            "user_type": "local",
            "role": "user",
            "display_name": "Test User",
        })
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "created_by": user_id,
        "agent_description": f"Description of {agent_name}",
        "agent_type": "default",
    })
    # Derive unique IDs from agent_id suffix so multiple agents don't collide.
    suffix = agent_id.split("_", 1)[1] if "_" in agent_id else agent_id
    nar_id = f"nar_{suffix}"
    inst_id = f"inst_{suffix}"
    evt_id_1 = f"evt_{suffix}01"
    evt_id_2 = f"evt_{suffix}02"
    await db.insert("narratives", {
        "narrative_id": nar_id,
        "type": "chat",
        "agent_id": agent_id,
        "narrative_info": json.dumps({"title": "Test Chat"}),
        "main_chat_instance_id": inst_id,
        "round_counter": 0,
    })
    await db.insert("events", {
        "event_id": evt_id_1,
        "trigger": "user_input",
        "trigger_source": "test",
        "agent_id": agent_id,
        "user_id": user_id,
        "narrative_id": nar_id,
        "final_output": f"Hello from {agent_id} reaching out to {evt_id_1} in {nar_id}",
    })
    await db.insert("events", {
        "event_id": evt_id_2,
        "trigger": "user_input",
        "trigger_source": "test",
        "agent_id": agent_id,
        "user_id": user_id,
        "narrative_id": nar_id,
        "final_output": f"Second event referencing {nar_id}",
    })
    await db.insert("module_instances", {
        "instance_id": inst_id,
        "module_class": "ChatModule",
        "agent_id": agent_id,
        "user_id": user_id,
        "is_public": 0,
        "status": "active",
    })
    await db.insert("instance_social_entities", {
        "instance_id": inst_id,
        "entity_id": "external_user_42",
        "entity_type": "user",
        "entity_name": "External User",
        "entity_description": "A non-agent contact",
        "familiarity": "known_of",
    })


@pytest.mark.asyncio
async def test_id_kinds_registry_consistent():
    """Every kind in ID_KINDS must produce a regex that matches its own gen_new_id."""
    from xyz_agent_context.bundle.id_field_map import gen_new_id, ID_KIND_PREFIXES
    for kind in ID_KIND_PREFIXES:
        sample = gen_new_id(kind)
        pattern = ID_KINDS[kind]
        assert re.fullmatch(pattern, sample), (
            f"gen_new_id('{kind}') produced {sample!r} which does NOT match "
            f"ID_KINDS['{kind}'] = {pattern!r}"
        )


@pytest.mark.asyncio
async def test_all_id_regex_compiles():
    """Total alternation regex must compile and match a sample of each kind."""
    rgx = build_all_id_regex()
    for kind, pattern in ID_KINDS.items():
        # generate one synthetic sample matching the regex
        # Use 12 hex chars for the suffix (within the {8,16} range)
        prefix = pattern.split("_[")[0]
        sample = f"{prefix}_a1b2c3d4e5f6"
        assert rgx.search(sample), f"unified regex didn't match {sample!r} for kind {kind}"


@pytest.mark.asyncio
async def test_full_roundtrip(db_client, tmp_workspace_root):
    """Seed an agent, export, import, assert all IDs were rewritten coherently."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    orig_agent_id = "agent_aaaa0001bbbb"
    orig_nar_id = "nar_aaaa0001bbbb"  # matches what _seed_agent derives
    await _seed_agent(db_client, orig_agent_id, "Test Agent", user_id)

    # Create the workspace dir so builder._pack_workspace finds it
    ws = tmp_workspace_root / f"{orig_agent_id}_{user_id}"
    ws.mkdir()
    (ws / "notes.md").write_text(
        f"Some notes mentioning {orig_agent_id} and {orig_nar_id}.\n", encoding="utf-8"
    )

    bundle_path = tmp_workspace_root.parent / "test.nxbundle"
    selection = ExportSelection(agent_ids=[orig_agent_id])
    result = await build_bundle(user_id, selection, bundle_path)
    assert bundle_path.exists() and bundle_path.stat().st_size > 0
    assert "warnings" in result
    assert orig_agent_id in result["manifest"]["agents"]

    # Import (same DB, will trigger a name clash → "(1)" suffix)
    pre = await preflight(bundle_path, user_id)
    assert pre["preflight_token"]
    # Original agent_name is "Test Agent" — DB already has it → expect 1 clash
    assert any(c["agent_name"] == "Test Agent" for c in pre["name_clashes"]) or pre["name_clashes"]
    summary = await confirm(pre["preflight_token"], user_id)

    assert summary["agents_created"] == 1
    assert summary["agents_renamed"] >= 1, "name suffix should have triggered"

    # Find the imported agent in the DB — it has a different agent_id
    rows = await db_client.get("agents", {"created_by": user_id})
    new_agents = [r for r in rows if r["agent_id"] != orig_agent_id]
    assert len(new_agents) == 1
    new_aid = new_agents[0]["agent_id"]
    assert new_aid != orig_agent_id, "ID rewrite did NOT occur"
    assert re.fullmatch(ID_KINDS["agent"], new_aid), f"new agent_id {new_aid} not regex-valid"
    assert new_agents[0]["agent_name"] == "Test Agent (1)"

    # Narratives: rewritten + agent_id pointing at new_aid
    nrows = await db_client.get("narratives", {"agent_id": new_aid})
    assert len(nrows) == 1
    new_nar_id = nrows[0]["narrative_id"]
    assert new_nar_id != orig_nar_id
    assert re.fullmatch(ID_KINDS["narrative"], new_nar_id)

    # Events: rewritten event_id + narrative_id pointing at new_nar_id
    erows = await db_client.get("events", {"agent_id": new_aid})
    assert len(erows) == 2
    for e in erows:
        assert re.fullmatch(ID_KINDS["event"], e["event_id"])
        assert e["narrative_id"] == new_nar_id, "narrative_id wasn't remapped via Layer 2"
        # Layer 4: free text inside final_output should have any old IDs rewritten
        if orig_agent_id in (e["final_output"] or ""):
            pytest.fail(f"Layer 4 rewrite missed: {orig_agent_id} remained in final_output: {e['final_output']!r}")
        if orig_nar_id in (e["final_output"] or ""):
            pytest.fail(f"Layer 4 rewrite missed: {orig_nar_id} remained in final_output: {e['final_output']!r}")

    # Module instance + social entity: instance_id rewritten and consistent
    inst_rows = await db_client.get("module_instances", {"agent_id": new_aid})
    assert len(inst_rows) == 1
    new_inst = inst_rows[0]["instance_id"]
    assert new_inst != "inst_aaaa0001bbbb"  # original derived from agent_id suffix
    assert re.fullmatch(ID_KINDS["instance"], new_inst)

    se_rows = await db_client.get("instance_social_entities", {"instance_id": new_inst})
    assert len(se_rows) == 1
    # entity_type='user' so entity_id is NOT an agent — should be untouched
    assert se_rows[0]["entity_id"] == "external_user_42"
    # but instance_id should be the new one
    assert se_rows[0]["instance_id"] == new_inst

    # Workspace tar was extracted to canonical path
    new_ws = tmp_workspace_root / f"{new_aid}_{user_id}"
    assert new_ws.is_dir(), f"workspace not extracted to {new_ws}"
    notes_after = (new_ws / "notes.md").read_text(encoding="utf-8")
    # Layer 4 should rewrite IDs even in workspace text files
    assert orig_agent_id not in notes_after
    assert orig_nar_id not in notes_after


@pytest.mark.asyncio
async def test_no_dangling_references(db_client, tmp_workspace_root):
    """Bundle import must produce a graph with no agent_id references outside the imported set."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    await _seed_agent(db_client, "agent_aaaaaa11", "Alpha", user_id)
    await _seed_agent(db_client, "agent_bbbbbb22", "Beta", user_id)

    # Both have workspace dirs
    for aid in ["agent_aaaaaa11", "agent_bbbbbb22"]:
        (tmp_workspace_root / f"{aid}_{user_id}").mkdir()

    bundle_path = tmp_workspace_root.parent / "two.nxbundle"
    await build_bundle(
        user_id, ExportSelection(agent_ids=["agent_aaaaaa11", "agent_bbbbbb22"]), bundle_path
    )

    pre = await preflight(bundle_path, user_id)
    await confirm(pre["preflight_token"], user_id)

    # Collect every imported agent_id (originals stay, plus 2 new ones)
    rows = await db_client.get("agents", {"created_by": user_id})
    all_aids = {r["agent_id"] for r in rows}
    new_aids = all_aids - {"agent_aaaaaa11", "agent_bbbbbb22"}
    assert len(new_aids) == 2

    # Every event/narrative/instance for the new agents should reference only new_aids
    for new_aid in new_aids:
        ne = await db_client.get("events", {"agent_id": new_aid})
        for e in ne:
            assert e["agent_id"] in new_aids
        nn = await db_client.get("narratives", {"agent_id": new_aid})
        for n in nn:
            assert n["agent_id"] in new_aids
        ni = await db_client.get("module_instances", {"agent_id": new_aid})
        for i in ni:
            assert i["agent_id"] in new_aids


@pytest.mark.asyncio
async def test_jobs_preserve_timestamps_on_import(db_client, tmp_workspace_root):
    """Regression: instance_jobs.created_at / updated_at must survive the
    roundtrip. The schema has no DB default for these columns, so popping
    them on import inserts NULL — and JobModel.created_at is a non-Optional
    datetime, so job_trigger blows up on first poll. See importer.py jobs
    section: we must keep the bundle's timestamps (and only backfill if
    missing)."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_jobttsmm00"
    await _seed_agent(db_client, aid, "Jobby", user_id)
    (tmp_workspace_root / f"{aid}_{user_id}").mkdir()

    # Add a JobModule instance + an instance_jobs row with explicit timestamps
    job_inst_id = "job_ttsmm0001"
    await db_client.insert("module_instances", {
        "instance_id": job_inst_id,
        "module_class": "JobModule",
        "agent_id": aid,
        "user_id": user_id,
        "is_public": 0,
        "status": "active",
    })
    src_created = "2026-04-15 10:00:00.000000"
    src_updated = "2026-04-16 11:30:00.000000"
    await db_client.insert("instance_jobs", {
        "instance_id": job_inst_id,
        "job_id": "job_ttsmm0099",
        "agent_id": aid,
        "user_id": user_id,
        "title": "Daily standup",
        "job_type": "ongoing",
        "status": "pending",
        "created_at": src_created,
        "updated_at": src_updated,
    })

    bundle_path = tmp_workspace_root.parent / "jobs.nxbundle"
    await build_bundle(user_id, ExportSelection(agent_ids=[aid]), bundle_path)

    pre = await preflight(bundle_path, user_id)
    await confirm(pre["preflight_token"], user_id)

    rows = await db_client.get("agents", {"created_by": user_id})
    new_aid = next(r["agent_id"] for r in rows if r["agent_id"] != aid)
    new_jobs = await db_client.get("instance_jobs", {"agent_id": new_aid})
    assert len(new_jobs) == 1
    j = new_jobs[0]
    assert j["created_at"] is not None and j["created_at"] != "", \
        f"created_at must survive import; got {j['created_at']!r}"
    assert j["updated_at"] is not None and j["updated_at"] != "", \
        f"updated_at must survive import; got {j['updated_at']!r}"
    # Should be the original timestamps verbatim (sqlite stores as-is)
    assert str(j["created_at"]).startswith("2026-04-15"), j["created_at"]
    assert str(j["updated_at"]).startswith("2026-04-16"), j["updated_at"]


@pytest.mark.asyncio
async def test_unknown_module_class_skipped_with_warning(db_client, tmp_workspace_root):
    """Importing a bundle that contains an unregistered module_class (e.g.
    MatrixModule from a custom build) must NOT create a zombie row that
    runtime would log "Unknown module type X, skipping" against forever.
    The module_instances row is dropped, its dependent instance_jobs cascade
    out, and a manifest warning is emitted."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_unkclas000"
    await _seed_agent(db_client, aid, "Stranger", user_id)
    (tmp_workspace_root / f"{aid}_{user_id}").mkdir()

    rogue_inst = "matrix_unkclasinst"
    await db_client.insert("module_instances", {
        "instance_id": rogue_inst,
        "module_class": "MatrixModule",  # NOT in MODULE_MAP
        "agent_id": aid,
        "user_id": user_id,
        "is_public": 0,
        "status": "active",
    })
    # Cascade target: a job pinned to that rogue instance
    await db_client.insert("instance_jobs", {
        "instance_id": rogue_inst,
        "job_id": "job_unkclas0099",
        "agent_id": aid,
        "user_id": user_id,
        "title": "Rogue job",
        "job_type": "ongoing",
        "status": "pending",
        "created_at": "2026-04-20 09:00:00.000000",
        "updated_at": "2026-04-20 09:00:00.000000",
    })

    bundle_path = tmp_workspace_root.parent / "rogue.nxbundle"
    await build_bundle(user_id, ExportSelection(agent_ids=[aid]), bundle_path)

    pre = await preflight(bundle_path, user_id)
    summary = await confirm(pre["preflight_token"], user_id)

    rows = await db_client.get("agents", {"created_by": user_id})
    new_aid = next(r["agent_id"] for r in rows if r["agent_id"] != aid)

    # Rogue MatrixModule instance was NOT inserted
    new_insts = await db_client.get("module_instances", {"agent_id": new_aid})
    assert all(i["module_class"] != "MatrixModule" for i in new_insts)

    # Its dependent job was also dropped (cascade)
    new_jobs = await db_client.get("instance_jobs", {"agent_id": new_aid})
    assert len(new_jobs) == 0, f"unknown-module job not dropped: {new_jobs}"

    # A warning surfaced
    assert any("MatrixModule" in w for w in summary.get("warnings", [])), \
        f"expected MatrixModule warning in summary.warnings, got {summary.get('warnings')!r}"


@pytest.mark.asyncio
async def test_artifacts_roundtrip_strips_and_restores_prefix(db_client, tmp_workspace_root):
    """instance_artifacts.file_path is stored relative to base_working_path
    (always starts with `{aid}_{user_id}/...`). The bundle must store the
    workspace-relative slice; the importer must reapply the new agent's
    prefix. Plus session_id is cleared and pinned forced to 1."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_artifac00"
    await _seed_agent(db_client, aid, "Drawer", user_id)
    ws = tmp_workspace_root / f"{aid}_{user_id}"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "work").mkdir()
    (ws / "work" / "output.html").write_text("<html>hello</html>", encoding="utf-8")

    art_id = "art_artif001"
    rel_path = f"{aid}_{user_id}/work/output.html"
    await db_client.insert("instance_artifacts", {
        "artifact_id": art_id,
        "agent_id": aid,
        "user_id": user_id,
        "session_id": "session_xyz",
        "original_session_id": None,
        "title": "Welcome page",
        "kind": "html",
        "description": "auto-generated welcome",
        "pinned": 0,
        "file_path": rel_path,
        "size_bytes": 19,
        "latest_version": 1,
        "created_at": "2026-05-15 10:00:00.000000",
        "updated_at": "2026-05-15 10:00:00.000000",
    })

    bundle_path = tmp_workspace_root.parent / "art.nxbundle"
    selection = ExportSelection(
        agent_ids=[aid],
        artifact_selection={aid: [art_id]},
    )
    await build_bundle(user_id, selection, bundle_path)

    # Inspect bundle on disk: artifacts.json must use workspace-relative path
    import zipfile
    with zipfile.ZipFile(bundle_path, "r") as zf:
        with zf.open(f"agents/{aid}/artifacts.json") as f:
            shipped = json.loads(f.read().decode("utf-8"))
    assert len(shipped) == 1
    assert shipped[0]["file_path"] == "work/output.html", (
        f"expected workspace-relative file_path; got {shipped[0]['file_path']!r}"
    )

    # Import as the SAME user — name clash forces a new agent_id
    pre = await preflight(bundle_path, user_id)
    summary = await confirm(pre["preflight_token"], user_id)
    assert summary["artifacts_created"] == 1, summary

    rows = await db_client.get("agents", {"created_by": user_id})
    new_aid = next(r["agent_id"] for r in rows if r["agent_id"] != aid)
    new_arts = await db_client.get("instance_artifacts", {"agent_id": new_aid})
    assert len(new_arts) == 1
    art = new_arts[0]
    assert art["artifact_id"] != art_id
    assert re.fullmatch(ID_KINDS["artifact"], art["artifact_id"])
    assert art["file_path"] == f"{new_aid}_{user_id}/work/output.html"
    assert art["session_id"] is None, f"session_id should be NULL; got {art['session_id']!r}"
    assert int(art["pinned"]) == 1, f"pinned should be forced to 1; got {art['pinned']!r}"
    assert art["user_id"] == user_id


@pytest.mark.asyncio
async def test_mcp_selection_default_is_empty(db_client, tmp_workspace_root):
    """Without mcp_selection, the bundle must ship zero MCP rows even when
    the agent has mcp_urls registered — MCP is opt-in by design."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_mcpemp000"
    await _seed_agent(db_client, aid, "MCP-less", user_id)
    (tmp_workspace_root / f"{aid}_{user_id}").mkdir()
    await db_client.insert("mcp_urls", {
        "mcp_id": "mcp_emp00001",
        "agent_id": aid,
        "user_id": user_id,
        "name": "TestMCP",
        "url": "http://example.invalid/mcp",
        "is_enabled": 1,
    })

    bundle_path = tmp_workspace_root.parent / "mcpempty.nxbundle"
    await build_bundle(user_id, ExportSelection(agent_ids=[aid]), bundle_path)
    pre = await preflight(bundle_path, user_id)
    summary = await confirm(pre["preflight_token"], user_id)

    assert summary.get("mcp_urls_created", 0) == 0
    assert summary.get("mcp_hints", 0) == 0


@pytest.mark.asyncio
async def test_mcp_selection_writes_through_with_reset_status(db_client, tmp_workspace_root):
    """When mcp_selection is provided, the importer creates real mcp_urls rows
    with rewritten mcp_id + agent_id and reset connection_status."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_mcpwrt00"
    await _seed_agent(db_client, aid, "MCP-writer", user_id)
    (tmp_workspace_root / f"{aid}_{user_id}").mkdir()
    src_mcp_id = "mcp_wrt00001"
    await db_client.insert("mcp_urls", {
        "mcp_id": src_mcp_id,
        "agent_id": aid,
        "user_id": user_id,
        "name": "WriterMCP",
        "url": "http://example.invalid/mcp/wr",
        "is_enabled": 1,
        "connection_status": "connected",
        "last_error": "stale info",
    })

    bundle_path = tmp_workspace_root.parent / "mcpwrt.nxbundle"
    selection = ExportSelection(
        agent_ids=[aid],
        mcp_selection={aid: [src_mcp_id]},
    )
    await build_bundle(user_id, selection, bundle_path)

    pre = await preflight(bundle_path, user_id)
    summary = await confirm(pre["preflight_token"], user_id)
    assert summary.get("mcp_urls_created", 0) == 1, summary

    rows = await db_client.get("agents", {"created_by": user_id})
    new_aid = next(r["agent_id"] for r in rows if r["agent_id"] != aid)
    new_mcps = await db_client.get("mcp_urls", {"agent_id": new_aid})
    assert len(new_mcps) == 1
    m = new_mcps[0]
    assert m["mcp_id"] != src_mcp_id
    assert re.fullmatch(ID_KINDS["mcp"], m["mcp_id"])
    assert m["name"] == "WriterMCP"
    assert m["url"] == "http://example.invalid/mcp/wr"
    assert m["user_id"] == user_id
    # connection_status reset so the poller revalidates locally
    assert (m["connection_status"] in (None, "")), (
        f"connection_status must be reset; got {m['connection_status']!r}"
    )
    assert (m["last_error"] in (None, "")), (
        f"last_error must be reset; got {m['last_error']!r}"
    )


@pytest.mark.asyncio
async def test_legacy_bundle_without_artifacts_or_mcps_imports_clean(db_client, tmp_workspace_root):
    """A 1.0-shaped bundle (no artifacts.json, mcp_hints.json that's just
    {name,url,...} without mcp_id) must still import without crashing and
    without auto-creating mcp_urls rows."""
    from xyz_agent_context.bundle.importer import preflight, confirm

    user_id = "test_user"
    aid = "agent_legacy001"
    # Hand-build a minimal bundle directory then zip it.
    import tempfile, zipfile
    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        (tmp / "agents" / aid).mkdir(parents=True)
        # Minimum agent record so importer's name-dedup path is exercised
        agent_record = {
            "agent_id": aid,
            "agent_name": "Legacy",
            "agent_type": "default",
            "agent_description": "legacy bundle",
            "created_by": "<original_owner>",
        }
        (tmp / "agents" / aid / "agent.json").write_text(
            json.dumps(agent_record), encoding="utf-8"
        )
        # Legacy mcp_hints style: no mcp_id, just hints to display.
        legacy_hints = [
            {"agent_id": aid, "name": "OldMCP", "url": "http://old.invalid"},
        ]
        (tmp / "mcp_hints.json").write_text(
            json.dumps(legacy_hints), encoding="utf-8"
        )
        manifest = {
            "bundle_format_version": "1.0",
            "exported_at": "2026-04-01T00:00:00+00:00",
            "owner_placeholder": "<original_owner>",
            "agents": [aid],
            "agents_summary": [],
            "skills": [],
            "stripped": [],
            "warnings": [],
            "info": [],
            "info_counters": {},
            "embedding": {"provider": None, "model": None, "dim": None},
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        bundle_path = tmp_workspace_root.parent / "legacy.nxbundle"
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in tmp.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(tmp)))

    pre = await preflight(bundle_path, user_id)
    summary = await confirm(pre["preflight_token"], user_id)

    # 1.0 bundle: write-through gated off; no mcp_urls rows created
    assert summary.get("mcp_urls_created", 0) == 0
    assert summary.get("mcp_hints", 0) == 1  # still surfaced as hints
    # No artifacts.json => no instance_artifacts rows
    assert summary.get("artifacts_created", 0) == 0
