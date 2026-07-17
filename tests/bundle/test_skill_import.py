"""
@file_name: test_skill_import.py
@author: NetMind.AI
@date: 2026-07-13
@description: full_copy skill import must land in the bundle's known skill_dir
             (not a random temp name) and preserve the skill's .skill_meta.json.

Regression for the arena bug (see
reference/self_notebook/plans/2026-07-13-skill-bundle-optimization.md §1):

- A skill is packed twice in a bundle: inside workspace.tar.gz (with its
  credentials.json stripped by the sensitive-file scanner) AND as a separate
  full_copy archive (unfiltered — carries credentials.json). On import the two
  are meant to collapse via install_skill's same-name overwrite.
- Bug A1: install_skill re-derived the target dir name from SKILL.md frontmatter,
  falling back to the extraction temp dir basename when the SKILL.md has no
  frontmatter (arena starts with `##`). So the full_copy landed in
  skills/tmpXXXX/ instead of skills/arena/ → no overwrite → two dirs, and the
  agent's skills/arena/ (from the workspace snapshot) has no credentials.json.
- Bug A2: install_skill's _save_skill_meta overwrote the incoming
  .skill_meta.json, wiping env_config (the env-var credentials).

Uses the same get_db_client-wired fixtures as test_roundtrip.py.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


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
    from xyz_agent_context.utils import db_factory

    db_factory._clients_by_loop.clear()
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id, agent_name, user_id="test_user"):
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert(
            "users",
            {
                "user_id": user_id,
                "user_type": "local",
                "role": "user",
                "display_name": "Test User",
            },
        )
    await db.insert(
        "agents",
        {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "created_by": user_id,
            "agent_description": "d",
            "agent_type": "default",
        },
    )


def _seed_skill_on_disk(ws_root: Path, agent_id: str, user_id: str, skill_dir: str):
    """Create skills/<skill_dir>/ with a NO-frontmatter SKILL.md (like arena),
    a credentials.json (a sensitive file), and a .skill_meta.json with env_config."""
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    skills = agent_workspace_path(agent_id, user_id, base=str(ws_root)) / "skills" / skill_dir
    skills.mkdir(parents=True, exist_ok=True)
    # No `---` frontmatter → install_skill can't derive a name from it.
    (skills / "SKILL.md").write_text(
        "## Arena skill\n\nCredentials live in `skills/arena/credentials.json`.\n",
        encoding="utf-8",
    )
    (skills / "credentials.json").write_text(
        json.dumps({"api_key": "SECRET_ARENA_KEY", "claim_token": "tok123"}),
        encoding="utf-8",
    )
    (skills / ".skill_meta.json").write_text(
        json.dumps(
            {
                "source_type": "zip",
                "requires": {"env": ["ARENA_API_KEY"], "bins": []},
                "env_config": {"ARENA_API_KEY": base64.b64encode(b"SECRET_ARENA_KEY").decode()},
                "study_result": "learned arena",
            }
        ),
        encoding="utf-8",
    )
    return skills


async def test_full_copy_skill_lands_in_skill_dir_with_credentials(db_client, tmp_workspace_root, tmp_path):
    """The imported agent must get exactly skills/arena/ with credentials.json and
    a preserved env_config — not a stray skills/tmpXXXX/ and a credential-less arena.
    """
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    aid, uid = "agent_skill0001", "test_user"
    await _seed_agent(db_client, aid, "SkillAgent", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    bundle = tmp_path / "b.nxbundle"
    selection = ExportSelection(
        agent_ids=[aid],
        skill_methods=[
            {
                "agent_id": aid,
                "skill_name": "arena",
                "skill_dir": "arena",
                "install_method": "full_copy",
            }
        ],
        include_skill_secrets=True,  # carry the credential to assert it lands right
    )
    await build_bundle(uid, selection, bundle)
    pre = await preflight(bundle, uid)
    summary = await confirm(pre["preflight_token"], uid)
    assert summary.get("skills_imported", 0) == 1

    # Find the imported (renamed) agent.
    agents = await db_client.get("agents", {"created_by": uid})
    new_aid = next(a["agent_id"] for a in agents if a["agent_id"] != aid)

    skills_root = agent_workspace_path(new_aid, uid, base=str(tmp_workspace_root)) / "skills"
    subdirs = sorted(p.name for p in skills_root.iterdir() if p.is_dir())

    # (1) exactly one skill dir, named by the known skill_dir — no tmp* stray.
    assert subdirs == ["arena"], f"expected only ['arena'], got {subdirs}"

    arena = skills_root / "arena"
    # (2) the credential file travelled into the right place.
    assert (arena / "credentials.json").exists()
    creds = json.loads((arena / "credentials.json").read_text())
    assert creds["api_key"] == "SECRET_ARENA_KEY"

    # (3) .skill_meta.json env_config preserved (not clobbered by _save_skill_meta).
    meta = json.loads((arena / ".skill_meta.json").read_text())
    assert meta.get("env_config", {}).get("ARENA_API_KEY"), "env_config was wiped on install"


def _full_copy_names(bundle: Path, aid: str, skill_dir: str) -> list[str]:
    """Filenames inside the full_copy archive for a skill."""
    with zipfile.ZipFile(bundle) as z:
        ref = f"skills/{aid}/{skill_dir}-full.zip"
        with zipfile.ZipFile(io.BytesIO(z.read(ref))) as inner:
            return inner.namelist()


def _manifest(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as z:
        return json.loads(z.read("manifest.json"))


async def test_dir_fix_holds_without_secrets(db_client, tmp_workspace_root, tmp_path):
    """Track A (correct dir name) is independent of Track B: even with secrets
    scrubbed, the skill still lands in exactly skills/arena/ (no tmp stray)."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    aid, uid = "agent_skill0002", "test_user"
    await _seed_agent(db_client, aid, "SkillAgent2", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "arena", "install_method": "full_copy"}
            ],
            # include_skill_secrets defaults False → scrub
        ),
        bundle,
    )
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    new_aid = next(a["agent_id"] for a in await db_client.get("agents", {"created_by": uid}) if a["agent_id"] != aid)
    skills_root = agent_workspace_path(new_aid, uid, base=str(tmp_workspace_root)) / "skills"
    assert sorted(p.name for p in skills_root.iterdir() if p.is_dir()) == ["arena"]
    # secret scrubbed → no credential file, env_config blanked
    assert not (skills_root / "arena" / "credentials.json").exists()
    meta = json.loads((skills_root / "arena" / ".skill_meta.json").read_text())
    assert meta.get("env_config", {}).get("ARENA_API_KEY", "") == ""


async def test_skill_secrets_scrubbed_on_export_by_default(db_client, tmp_workspace_root, tmp_path):
    """Default export scrubs skill secrets from BOTH the full_copy archive and
    the workspace snapshot; manifest reflects it."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_skill0003", "test_user"
    await _seed_agent(db_client, aid, "SkillAgent3", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "arena", "install_method": "full_copy"}
            ],
        ),
        bundle,
    )

    # full_copy archive: no credentials.json (sensitive filter)
    names = _full_copy_names(bundle, aid, "arena")
    assert "credentials.json" not in names
    assert "SKILL.md" in names  # structure kept
    m = _manifest(bundle)
    assert m.get("contains_skill_secrets") is False
    assert "skill_secrets" in m.get("stripped", [])


async def test_skill_secrets_kept_when_opted_in(db_client, tmp_workspace_root, tmp_path):
    """Opting in carries the credential file + marks the manifest."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_skill0004", "test_user"
    await _seed_agent(db_client, aid, "SkillAgent4", uid)
    _seed_skill_on_disk(tmp_workspace_root, aid, uid, "arena")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "arena", "skill_dir": "arena", "install_method": "full_copy"}
            ],
            include_skill_secrets=True,
        ),
        bundle,
    )

    names = _full_copy_names(bundle, aid, "arena")
    assert "credentials.json" in names
    m = _manifest(bundle)
    assert m.get("contains_skill_secrets") is True
    assert "skill_secrets" not in m.get("stripped", [])


def _seed_builtin_skill_on_disk(ws_root: Path, agent_id: str, user_id: str, skill_dir: str):
    """Create a built-in skill dir (`.skill_meta.json` builtin: true) on disk."""
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    skills = agent_workspace_path(agent_id, user_id, base=str(ws_root)) / "skills" / skill_dir
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "SKILL.md").write_text(f"---\nname: {skill_dir}\n---\n", encoding="utf-8")
    (skills / ".skill_meta.json").write_text(json.dumps({"source_type": "builtin", "builtin": True}), encoding="utf-8")
    return skills


async def test_builtin_skill_forced_to_builtin_method_despite_full_copy_request(
    db_client, tmp_workspace_root, tmp_path
):
    """A client asking for full_copy/zip on a built-in must NOT ship it as user
    data. The server-side guard detects the built-in on disk and downgrades it
    to the payload-less `builtin` method: the manifest records install_method
    'builtin' with no archive_ref, and the bundle carries no skill archive.
    """
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_builtin01", "test_user"
    await _seed_agent(db_client, aid, "BuiltinAgent", uid)
    _seed_builtin_skill_on_disk(tmp_workspace_root, aid, uid, "officecli")

    bundle = tmp_path / "b.nxbundle"
    # Client (bypassed frontend / bug) explicitly requests full_copy for a builtin.
    await build_bundle(
        uid,
        ExportSelection(
            agent_ids=[aid],
            skill_methods=[
                {"agent_id": aid, "skill_name": "officecli", "skill_dir": "officecli", "install_method": "full_copy"}
            ],
            include_skill_secrets=True,
        ),
        bundle,
    )

    m = _manifest(bundle)
    entries = [s for s in m.get("skills", []) if s.get("name") == "officecli"]
    assert len(entries) == 1, f"expected one officecli entry, got {entries}"
    entry = entries[0]
    # Guard forced it onto the builtin path — no user-data payload.
    assert entry.get("install_method") == "builtin"
    assert "archive_ref" not in entry
    # And no skill archive for it travelled inside the bundle.
    with zipfile.ZipFile(bundle) as z:
        assert not any(n.startswith("skills/") and "officecli" in n and n.endswith(".zip") for n in z.namelist())
