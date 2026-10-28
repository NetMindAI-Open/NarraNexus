"""
@file_name: test_export_symlinked_workspace.py
@author: Bin Liang
@date: 2026-10-28
@description: A workspace dir swapped for a symlink is refused at export, but
as a per-agent warning — the same shape the full_copy skill path already uses —
not as an exception that fails the whole multi-agent export with an
unattributed 500. The other agents still export, the refused agent exports no
workspace, and the warning names it.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest


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
async def db_client(tmp_path, monkeypatch):
    from narranexus.platform.settings import settings as core_settings

    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_path / 'nexus.db'}")
    from narranexus.platform.utils.db import db_factory

    db_factory._clients_by_loop.clear()
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


async def _seed_agent(db, agent_id, user_id):
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert("users", {
            "user_id": user_id, "user_type": "local", "role": "user", "display_name": "U",
        })
    await db.insert("agents", {
        "agent_id": agent_id, "agent_name": agent_id, "created_by": user_id,
        "agent_description": "d", "agent_type": "default",
    })


async def test_a_symlinked_workspace_is_a_per_agent_warning_not_a_failed_export(
    db_client, tmp_workspace_root, tmp_path
):
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

    uid = "test_user"
    good, linked = "agent_good00001", "agent_link00001"
    await _seed_agent(db_client, good, uid)
    await _seed_agent(db_client, linked, uid)

    good_ws = agent_workspace_path(good, uid, base=str(tmp_workspace_root))
    good_ws.mkdir(parents=True)
    (good_ws / "notes.md").write_text("mine\n", encoding="utf-8")

    victim_ws = agent_workspace_path("agent_vict00001", "user_victim", base=str(tmp_workspace_root))
    victim_ws.mkdir(parents=True)
    (victim_ws / "credentials.json").write_text('{"k": "victim-secret"}', encoding="utf-8")
    linked_ws = agent_workspace_path(linked, uid, base=str(tmp_workspace_root))
    os.symlink(victim_ws, linked_ws)

    bundle = tmp_path / "out.nxbundle"
    result = await build_bundle(uid, ExportSelection(agent_ids=[good, linked]), bundle)

    assert any(linked in w and "symlink" in w for w in result["warnings"]), result["warnings"]
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
    assert f"agents/{good}/workspace.tar.gz" in names
    assert f"agents/{linked}/workspace.tar.gz" not in names
    assert good in manifest["agents"] and linked in manifest["agents"]
