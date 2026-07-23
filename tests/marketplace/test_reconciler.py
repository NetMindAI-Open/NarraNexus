"""
@file_name: test_reconciler.py
@author: NetMind.AI
@date: 2026-07-21
@description: SkillSyncService drift-healing tests on a real (tmp) filesystem.

Covers all reconcile branches: manual dir -> row added; deleted dir ->
external_removed; hand-edited content -> modified; .disabled/ move ->
disabled; restored dir -> back to installed; reconcile_all walks the
nested {user}/{agent} workspace layout; idempotency (second pass clean).
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

import xyz_agent_context._skill_marketplace_impl.secret_box as secret_box_module
from xyz_agent_context._skill_marketplace_impl.install_pipeline import InstallPipeline
from xyz_agent_context.module.skill_module import SkillModule
from xyz_agent_context.repository.skill_installation_repository import (
    SkillInstallationRepository,
)
from xyz_agent_context.services.skill_sync_service import SkillSyncService

AGENT_ID = "agt_test"
USER_ID = "usr_test"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SKILL_SECRETS_KEY", raising=False)
    monkeypatch.setattr(secret_box_module, "_default_box", None)

    async def _noop_backup(**kwargs):
        return None

    import xyz_agent_context.bundle.skill_backup as skill_backup

    monkeypatch.setattr(skill_backup, "backup_after_api_install", _noop_backup)
    return tmp_path


def _make_zip(tmp_path: Path, name: str) -> Path:
    zip_path = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: 1.0.0\n---\nBody.\n",
        )
        zf.writestr(f"{name}/helper.txt", "hello")
    return zip_path


def _skills_dir() -> Path:
    return Path(SkillModule(agent_id=AGENT_ID, user_id=USER_ID).skills_dir)


async def _install(db_client, tmp_path, name="demo-skill"):
    await InstallPipeline(AGENT_ID, USER_ID, db_client=db_client).install_from_zip(
        _make_zip(tmp_path, name)
    )


async def _row(db_client, name):
    rows = await SkillInstallationRepository(db_client).list_for_workspace(AGENT_ID, USER_ID)
    return next((r for r in rows if r.skill_id == name), None)


@pytest.mark.asyncio
async def test_external_removed(db_client, workspace, tmp_path):
    await _install(db_client, tmp_path)
    shutil.rmtree(_skills_dir() / "demo-skill")

    stats = await SkillSyncService(db_client).reconcile_workspace(AGENT_ID, USER_ID)
    assert stats["external_removed"] == 1
    row = await _row(db_client, "demo-skill")
    assert row.status == "external_removed"
    assert row.last_event == "reconcile"


@pytest.mark.asyncio
async def test_manual_dir_gets_row(db_client, workspace, tmp_path):
    manual = _skills_dir() / "hand-made"
    manual.mkdir(parents=True)
    (manual / "SKILL.md").write_text("---\nname: hand-made\ndescription: d\n---\nBody.\n")

    stats = await SkillSyncService(db_client).reconcile_workspace(AGENT_ID, USER_ID)
    assert stats["added"] == 1
    row = await _row(db_client, "hand-made")
    assert row.source_type == "manual"
    assert row.status == "installed"


@pytest.mark.asyncio
async def test_hand_edit_marks_modified(db_client, workspace, tmp_path):
    await _install(db_client, tmp_path)
    (_skills_dir() / "demo-skill" / "helper.txt").write_text("tampered")

    stats = await SkillSyncService(db_client).reconcile_workspace(AGENT_ID, USER_ID)
    assert stats["modified"] == 1
    assert (await _row(db_client, "demo-skill")).status == "modified"


@pytest.mark.asyncio
async def test_disabled_dir_marks_disabled_and_restore(db_client, workspace, tmp_path):
    await _install(db_client, tmp_path)
    module = SkillModule(agent_id=AGENT_ID, user_id=USER_ID)
    module.disable_skill("demo-skill")

    service = SkillSyncService(db_client)
    stats = await service.reconcile_workspace(AGENT_ID, USER_ID)
    assert stats["disabled"] == 1
    assert (await _row(db_client, "demo-skill")).status == "disabled"

    module.enable_skill("demo-skill")
    stats = await service.reconcile_workspace(AGENT_ID, USER_ID)
    assert stats["restored"] == 1
    assert (await _row(db_client, "demo-skill")).status == "installed"


@pytest.mark.asyncio
async def test_reconcile_never_touches_files(db_client, workspace, tmp_path):
    await _install(db_client, tmp_path)
    (_skills_dir() / "demo-skill" / "helper.txt").write_text("tampered")

    before = sorted(p.as_posix() for p in _skills_dir().rglob("*"))
    await SkillSyncService(db_client).reconcile_workspace(AGENT_ID, USER_ID)
    after = sorted(p.as_posix() for p in _skills_dir().rglob("*"))
    assert before == after
    assert (_skills_dir() / "demo-skill" / "helper.txt").read_text() == "tampered"


@pytest.mark.asyncio
async def test_reconcile_all_walks_nested_layout_and_is_idempotent(
    db_client, workspace, tmp_path
):
    await _install(db_client, tmp_path)
    shutil.rmtree(_skills_dir() / "demo-skill")

    service = SkillSyncService(db_client)
    totals = await service.reconcile_all()
    assert totals["workspaces"] == 1
    assert totals["external_removed"] == 1

    totals2 = await service.reconcile_all()
    assert totals2["external_removed"] == 0
    assert totals2["added"] == 0


@pytest.mark.asyncio
async def test_meta_json_survives_reconcile_source_fields(db_client, workspace, tmp_path):
    """A manual copy WITH .skill_meta.json keeps its recorded provenance."""
    manual = _skills_dir() / "copied-skill"
    manual.mkdir(parents=True)
    (manual / "SKILL.md").write_text("---\nname: copied-skill\ndescription: d\n---\nBody.\n")
    (manual / ".skill_meta.json").write_text(
        json.dumps({"source_type": "github", "source_url": "https://github.com/a/b",
                    "version": "0.9.0"})
    )

    await SkillSyncService(db_client).reconcile_workspace(AGENT_ID, USER_ID)
    row = await _row(db_client, "copied-skill")
    assert row.source_type == "github"
    assert row.source_url == "https://github.com/a/b"
    assert row.version == "0.9.0"
