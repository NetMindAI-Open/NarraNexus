"""
@file_name: test_agent_field_normalization.py
@author: NarraNexus
@date: 2026-08-17
@description: Import-time normalization of agent_name / agent_description.

The bundle importer raw-inserts the `agents` row, bypassing
`AgentRepository`, so it has to normalize at its own edge. A row that lands
holding " 小绿 " can never be cleaned up afterwards: `agent_field_matches`
compares normalized values, so the owner saving "小绿" is judged a no-op, no
write is issued, and the endpoint then certifies the untouched row —
success, no error, nothing logged. This path has no feature flag: bundle
import and team-marketplace install both reach it.

The ORDER is the contract, and it is what these tests exist for:

    normalize → dedupe → normalize + clamp again

- normalize BEFORE dedupe, or a name with stray whitespace fails to match the
  already-normalized row it is supposed to be deduped against.
- normalize AND clamp again AFTER dedupe, because the " (n)" suffix has
  neither a length budget nor a whitespace budget of its own: 255 chars
  becomes 259, and an empty candidate becomes " (1)" with a leading space.

Asserting only the final value would stay green with the normalize call moved
after dedupe, so `test_whitespace_name_dedupes_against_the_normalized_row`
uses the one input that can tell the two orders apart.

Sibling of test_agent_field_length.py (the clamp half); fixtures mirror it.
"""

import pytest

from narranexus.platform.schema import agent_field_matches
from narranexus.platform.schema.entity_schema import AGENT_TEXT_MAX_LENGTH


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from narranexus.platform.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from narranexus.platform.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")
    from narranexus.platform.utils.db import db_factory

    db_factory._clients_by_loop.clear()
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id, user_id, name, description=""):
    """Raw-insert, exactly as a foreign bundle's row would arrive."""
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert(
            "users",
            {
                "user_id": user_id,
                "user_type": "local",
                "role": "user",
                "display_name": "Seed User",
            },
        )
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": name,
            "created_by": user_id,
            "agent_description": description,
            "agent_type": "default",
        },
    )


async def _export_then_import(db, tmp_workspace_root, agent_id, owner_id, importer_id):
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    ws = tmp_workspace_root / f"{agent_id}_{owner_id}"
    ws.mkdir()
    (ws / "notes.md").write_text("workspace notes\n", encoding="utf-8")

    bundle_path = tmp_workspace_root.parent / f"{agent_id}.nxbundle"
    await build_bundle(owner_id, ExportSelection(agent_ids=[agent_id]), bundle_path)

    pre = await preflight(bundle_path, importer_id)
    summary = await confirm(pre["preflight_token"], importer_id)
    rows = await db.get("agents", {"created_by": importer_id})
    return summary, rows


@pytest.mark.asyncio
async def test_import_stores_normalized_name_and_description(
    db_client, tmp_workspace_root
):
    await _seed_agent(
        db_client, "agent_ws0001name", "owner", "  小绿  ", "  精通各地美食推荐  "
    )
    _, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0001name", "owner", "importer"
    )

    assert len(rows) == 1
    imported = rows[0]
    assert imported["agent_name"] == "小绿"
    assert imported["agent_description"] == "精通各地美食推荐"


@pytest.mark.asyncio
async def test_an_imported_row_is_still_renameable(db_client, tmp_workspace_root):
    """The property that actually matters — stated the way the update path
    asks it. An unnormalized stored name makes the rename a silent no-op."""
    await _seed_agent(db_client, "agent_ws0002name", "owner", " 小绿 ")
    _, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0002name", "owner", "importer2"
    )

    from narranexus.platform.repository.agent_repository import AgentRepository

    stored = await AgentRepository(db_client).get_agent(rows[0]["agent_id"])
    assert agent_field_matches(stored, "agent_name", "小绿"), (
        "the imported row does not compare equal to its own normalized name, "
        "so renaming it to that name would issue no write and still report "
        "success — the row is stuck"
    )


@pytest.mark.asyncio
async def test_whitespace_name_dedupes_against_the_normalized_row(
    db_client, tmp_workspace_root
):
    """The one input that distinguishes normalize-before-dedupe from after.

    The importer already owns a row named "小绿". The incoming bundle carries
    "  小绿  ". Normalized first, the two collide and dedupe renames the
    import; normalized afterwards, they look different, no rename happens, and
    the owner ends up with two rows that render identically.
    """
    await _seed_agent(db_client, "agent_ws0003src", "owner", "  小绿  ")
    await _seed_agent(db_client, "agent_ws0003own", "importer3", "小绿")

    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0003src", "owner", "importer3"
    )

    # THIS is the discriminating assertion. Verified by moving the normalize
    # call after dedupe and re-running: the row then stores "小绿" — no
    # collision was seen, so no suffix was appended, and the owner is left with
    # two rows rendering identically.
    imported = [r for r in rows if r["agent_id"] != "agent_ws0003own"]
    assert len(imported) == 1
    assert imported[0]["agent_name"] == "小绿 (1)", (
        "the incoming name did not collide with the existing normalized row — "
        "normalization must run BEFORE dedupe"
    )
    # Deliberately NOT the discriminator: with the wrong order this still
    # reads 1, because post-dedupe normalization makes final_name differ from
    # the unnormalized clamped_name and `renamed` goes true for the wrong
    # reason. Kept only to pin that the rename is reported at all.
    assert summary["agents_renamed"] == 1


@pytest.mark.asyncio
async def test_dedupe_suffix_on_an_empty_name_is_normalized_too(
    db_client, tmp_workspace_root
):
    """dedupe_name has no whitespace budget: "" + " (1)" = " (1)".

    Re-normalizing after dedupe is what keeps that from becoming the one
    remaining way to land an unnormalized agents row.
    """
    await _seed_agent(db_client, "agent_ws0004src", "owner", "   ")
    await _seed_agent(db_client, "agent_ws0004own", "importer4", "")

    _, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0004src", "owner", "importer4"
    )

    imported = [r for r in rows if r["agent_id"] != "agent_ws0004own"]
    assert len(imported) == 1
    assert imported[0]["agent_name"] == "(1)", (
        f"stored {imported[0]['agent_name']!r} — a leading space survived the "
        f"dedupe suffix, which is an unrenameable row"
    )


@pytest.mark.asyncio
async def test_normalization_does_not_disturb_the_clamp(
    db_client, tmp_workspace_root
):
    """Both halves still hold together: padded AND over-long."""
    await _seed_agent(
        db_client, "agent_ws0005name", "owner", "   " + "N" * 300 + "   "
    )
    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0005name", "owner", "importer5"
    )

    name = rows[0]["agent_name"]
    assert len(name) == AGENT_TEXT_MAX_LENGTH
    assert name == "N" * AGENT_TEXT_MAX_LENGTH
    assert summary["agent_fields_trimmed"], "the clamp must still report itself"


@pytest.mark.asyncio
async def test_an_empty_bundle_name_gets_the_import_fallback(
    db_client, tmp_workspace_root
):
    """The importer was the last of the five creation paths able to store an
    empty name. Stored empty, the agent renders as a bare agent_id everywhere;
    a bundle is user-supplied input, so a blank agent_name in agent.json is
    ordinary rather than exotic."""
    await _seed_agent(db_client, "agent_ws0006src", "owner", "   ")
    _, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_ws0006src", "owner", "importer6"
    )

    assert len(rows) == 1
    assert rows[0]["agent_name"] == "Imported Agent", (
        "an empty name reached the row — the fallback the other four creation "
        "paths have is missing here"
    )


@pytest.mark.asyncio
async def test_a_dedupe_rename_corrects_the_imported_identity_memory(
    db_client, tmp_workspace_root
):
    """Importing under a colliding name IS a rename, and the profile comes along
    verbatim.

    The importer clamps, appends a dedupe suffix and falls back on an empty
    name — and copies `instance_awareness` row for row. So a bundle imported
    beside an agent of the same name lands with `agents.agent_name` = "小绿 (1)"
    while its own profile keeps declaring 小绿: exactly Shenzhen round 2, arriving
    through the import path instead of a rename path.

    The writer allowlist in tests/schema/test_only_one_writer_of_agent_name.py
    justified this file as a creation path with "no previous name to correct" —
    which was not true of it, and a gate is only worth its reasons.
    """
    from narranexus_plugins.awareness_module import IDENTITY_CHANGE_SECTION

    await _seed_agent(db_client, "agent_idm0001src", "owner", "小绿")
    await _seed_agent(db_client, "agent_idm0001own", "importer_idm", "小绿")

    # The source agent's own profile declares the name it had.
    await db_client.insert("module_instances", {
        "instance_id": "aware_idm_src", "agent_id": "agent_idm0001src",
        "user_id": "owner", "module_class": "AwarenessModule", "status": "active",
    })
    await db_client.insert("instance_awareness", {
        "instance_id": "aware_idm_src",
        "awareness": "# Agent Awareness Profile\n\n## 4. Role and Identity\n- 名称：小绿\n",
    })

    summary, rows = await _export_then_import(
        db_client, tmp_workspace_root, "agent_idm0001src", "owner", "importer_idm"
    )

    imported = [r for r in rows if r["agent_id"] != "agent_idm0001own"]
    assert len(imported) == 1
    assert imported[0]["agent_name"] == "小绿 (1)", "precondition: the import renamed"

    inst = await db_client.get(
        "module_instances",
        {"agent_id": imported[0]["agent_id"], "module_class": "AwarenessModule"},
    )
    assert inst, "the imported agent has no Awareness instance to correct"
    profile = (await db_client.get_one(
        "instance_awareness", {"instance_id": inst[0]["instance_id"]}
    ))["awareness"]

    assert IDENTITY_CHANGE_SECTION in profile, (
        "the imported profile still declares the pre-import name with nothing "
        "contradicting it"
    )
    assert "You are 「小绿 (1)」" in profile
