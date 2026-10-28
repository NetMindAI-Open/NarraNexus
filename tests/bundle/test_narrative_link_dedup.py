"""
@file_name: test_narrative_link_dedup.py
@author: Bin Liang
@date: 2026-08-19
@description: Bundle import must survive a REAL cross-agent instance_narrative_links
             unique-constraint collision (the importer's "case 2" except branch).

The importer inserts `instance_narrative_links` rows per agent. When two agents in
one bundle share the same instance and narrative, both link rows rewrite to the
SAME new (instance_id, narrative_id) pair via id_map: the first agent's loop
inserts it, the second agent's loop trips the composite UNIQUE
`uk_instance_narrative(instance_id, narrative_id)`. The importer catches THAT
specific collision (via the shared `is_unique_violation` predicate — PR#327 I1),
counts it as a skipped duplicate, and keeps going instead of aborting confirm().

This exercises a REAL driver-raised UNIQUE error from aiosqlite (not a mocked
exception), which is the whole point: it proves the real conflict text is
classified as a duplicate by the shared predicate. If the predicate stops
matching (e.g. the importer's over-broad→full-phrase tightening is reverted to
something that no longer recognises the real text), the collision re-raises,
confirm() rolls back, and this test goes red.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Keep the importer's workspace/skill writes off the real ~/.nexusagent."""
    from narranexus.platform.settings import settings as core_settings

    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def wired_db(tmp_db_path, monkeypatch):
    """A file-backed sqlite client that is ALSO what `get_db_client()` returns.

    `importer.confirm()` fetches its own client via `get_db_client()`, so the
    conftest in-memory fixture (not wired into db_factory) would not be the DB
    the import writes to. Patch settings + clear the per-loop cache like the
    roundtrip test does so the test and the importer share one DB.
    """
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


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _build_shared_instance_workdir(work_dir: Path) -> dict:
    """Lay out a two-agent bundle whose link rows collide after id rewrite.

    Agent A owns the narrative + instance and links them. Agent B carries the
    SAME (instance_id, narrative_id) link row but no dirs of its own — exactly
    the "cross-agent shared instance" shape the importer's case-2 branch guards.
    Both old ids land in id_map (A's dirs seed them) and rewrite to one pair, so
    A inserts and B collides. Returns the manifest.
    """
    aid_a = "agent_shareaaaa01"
    aid_b = "agent_sharebbbb02"
    inst_id = "inst_shareaaaa01"
    nar_id = "nar_shareaaaa01"

    # Agent A: agent.json + narrative + instance + the link
    _write_json(
        work_dir / "agents" / aid_a / "agent.json",
        {"agent_id": aid_a, "agent_name": "Share A", "agent_type": "default",
         "agent_description": "shares an instance"},
    )
    _write_json(
        work_dir / "agents" / aid_a / "narratives" / nar_id / "narrative.json",
        {"narrative_id": nar_id, "type": "chat", "agent_id": aid_a,
         "narrative_info": json.dumps({"title": "Shared"}), "round_counter": 0},
    )
    _write_json(
        work_dir / "agents" / aid_a / "instances" / "ChatModule" / f"{inst_id}.json",
        {"instance_id": inst_id, "module_class": "ChatModule", "agent_id": aid_a,
         "is_public": 0, "status": "active"},
    )
    _write_json(
        work_dir / "agents" / aid_a / "instance_narrative_links.json",
        [{"instance_id": inst_id, "narrative_id": nar_id, "link_type": "active"}],
    )

    # Agent B: only agent.json + the SAME link row (no narrative/instance dirs)
    _write_json(
        work_dir / "agents" / aid_b / "agent.json",
        {"agent_id": aid_b, "agent_name": "Share B", "agent_type": "default",
         "agent_description": "reuses A's instance"},
    )
    _write_json(
        work_dir / "agents" / aid_b / "instance_narrative_links.json",
        [{"instance_id": inst_id, "narrative_id": nar_id, "link_type": "active"}],
    )

    # Order matters: A must be processed first so its row is in the DB when B
    # tries to insert the duplicate.
    return {"bundle_format_version": "1.1", "agents": [aid_a, aid_b]}


async def _seed_session(db, work_dir: Path, manifest: dict, user_id: str) -> str:
    """Register a preflight session pointing at the hand-built work_dir."""
    existing = await db.get_one("users", {"user_id": user_id})
    if not existing:
        await db.insert("users", {
            "user_id": user_id, "user_type": "local", "role": "user",
            "display_name": "Test User",
        })
    token = uuid.uuid4().hex
    await db.insert("bundle_preflight_sessions", {
        "token": token,
        "user_id": user_id,
        "work_dir": str(work_dir),
        "manifest_json": json.dumps(manifest, ensure_ascii=False),
    })
    return token


@pytest.mark.asyncio
async def test_cross_agent_shared_link_is_deduped_not_aborted(wired_db, tmp_path):
    from narranexus.platform.bundle.importer import confirm

    user_id = "test_user"
    work_dir = tmp_path / "wd"
    manifest = _build_shared_instance_workdir(work_dir)
    token = await _seed_session(wired_db, work_dir, manifest, user_id)

    # confirm() must NOT raise: the second agent's duplicate link is caught by
    # the case-2 except branch (real aiosqlite UNIQUE error → is_unique_violation).
    summary = await confirm(token, user_id)

    assert summary["agents_created"] == 2

    # Only the first insert survived; the collision was skipped, not written.
    assert summary["narrative_links_created"] == 1, (
        f"expected exactly one link row written, got "
        f"{summary['narrative_links_created']}"
    )
    link_rows = await wired_db.get("instance_narrative_links", {})
    assert len(link_rows) == 1, (
        f"the shared pair must exist exactly once, got {len(link_rows)} rows"
    )

    # The skipped duplicate is surfaced as a warning.
    assert any(
        "duplicate instance_narrative_links" in w for w in summary["warnings"]
    ), f"no skipped-duplicate warning in {summary['warnings']!r}"
