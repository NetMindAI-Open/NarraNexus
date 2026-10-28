"""
@file_name: test_user_edit.py
@date: 2026-08-19
@description: TDD for the user-edit save pipeline (spec A §3).

Contract under test:
- save_user_content is the ONE commit path for user edits from artifact
  editing surfaces: base_hash optimistic lock → atomic write (temp +
  os.replace) → pointer row refresh (hash/size/updated_at, file_path
  UNCHANGED) → history action="user_edited" → staged "updated" event.
- A stale base_hash raises ArtifactEditConflict (code 409) carrying the
  current hash, and the file on disk is untouched.
- Only kinds whose edit surface exists may be written (md/csv/html);
  everything else is rejected before touching the disk.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

from narranexus.platform.artifact import ArtifactService
from narranexus.platform.artifact._artifact_impl.errors import (
    ArtifactEditConflict,
    ArtifactKindMismatch,
    ArtifactTooLarge,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from narranexus.platform.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    entry = workspace / "notes.md"
    entry.write_text("# old\n", encoding="utf-8")

    repo = ArtifactRepository(db_client)
    art = Artifact(
        artifact_id="art_editme01",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="notes",
        kind="text/markdown",
        file_path=f"{WS_REL}/notes.md",
        size_bytes=entry.stat().st_size,
        content_hash=_sha("# old\n"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repo.create(art)

    yield {
        "db": db_client,
        "repo": repo,
        "service": ArtifactService(db_client),
        "art": art,
        "entry": entry,
        "workspace": workspace,
    }


async def _history_actions(db, artifact_id: str):
    rows = await db.execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s ORDER BY id",
        params=(artifact_id,),
        fetch=True,
    )
    return [r["action"] for r in rows]


async def _staged_actions(db, agent_id: str = "agent_x"):
    rows = await db.execute(
        "SELECT payload_json FROM instance_artifact_events WHERE agent_id = %s ORDER BY id",
        params=(agent_id,),
        fetch=True,
    )
    return [json.loads(r["payload_json"])["action"] for r in rows]


async def test_save_writes_content_and_commits(env):
    svc: ArtifactService = env["service"]
    updated = await svc.save_user_content(
        agent_id="agent_x",
        artifact_id="art_editme01",
        content="# new\n\nbody\n",
        base_hash=_sha("# old\n"),
    )
    # disk is truth: the entry file holds the new bytes
    assert env["entry"].read_text(encoding="utf-8") == "# new\n\nbody\n"
    # pointer row refreshed, pointer itself unchanged
    row = await env["repo"].get_by_id("art_editme01")
    assert row.file_path == env["art"].file_path
    assert row.content_hash == _sha("# new\n\nbody\n")
    assert row.size_bytes == len("# new\n\nbody\n".encode("utf-8"))
    assert updated.content_hash == row.content_hash
    # attribution + event
    assert await _history_actions(env["db"], "art_editme01") == ["user_edited"]
    assert await _staged_actions(env["db"]) == ["updated"]


async def test_stale_base_hash_conflicts_and_leaves_disk_alone(env):
    svc: ArtifactService = env["service"]
    with pytest.raises(ArtifactEditConflict) as exc:
        await svc.save_user_content(
            agent_id="agent_x",
            artifact_id="art_editme01",
            content="# lost update\n",
            base_hash=_sha("something stale"),
        )
    assert exc.value.code == 409
    # the conflict carries the CURRENT hash so the client can re-base
    assert exc.value.current_hash == _sha("# old\n")
    assert env["entry"].read_text(encoding="utf-8") == "# old\n"
    assert await _history_actions(env["db"], "art_editme01") == []
    assert await _staged_actions(env["db"]) == []


async def test_conflict_verifies_against_disk_not_table(env):
    """An external writer changed the file after our last commit point: the
    lock must compare against the DISK content (truth), not the stale table
    fingerprint — otherwise a save silently overwrites the external edit."""
    env["entry"].write_text("# externally changed\n", encoding="utf-8")
    svc: ArtifactService = env["service"]
    with pytest.raises(ArtifactEditConflict) as exc:
        await svc.save_user_content(
            agent_id="agent_x",
            artifact_id="art_editme01",
            content="# from the editor\n",
            base_hash=_sha("# old\n"),  # matches the table, NOT the disk
        )
    assert exc.value.current_hash == _sha("# externally changed\n")
    assert env["entry"].read_text(encoding="utf-8") == "# externally changed\n"


async def test_non_editable_kind_rejected_before_disk(env):
    repo: ArtifactRepository = env["repo"]
    png = env["workspace"] / "pic.png"
    png.write_bytes(b"\x89PNG fake")
    await repo.create(Artifact(
        artifact_id="art_pngnope1",
        agent_id="agent_x",
        user_id="user_y",
        session_id="sess_1",
        title="pic",
        kind="image/png",
        file_path=f"{WS_REL}/pic.png",
        size_bytes=9,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    svc: ArtifactService = env["service"]
    with pytest.raises(ArtifactKindMismatch):
        await svc.save_user_content(
            agent_id="agent_x",
            artifact_id="art_pngnope1",
            content="not an image",
            base_hash="whatever",
        )
    assert png.read_bytes() == b"\x89PNG fake"


async def test_oversize_content_rejected(env):
    svc: ArtifactService = env["service"]
    with pytest.raises(ArtifactTooLarge):
        await svc.save_user_content(
            agent_id="agent_x",
            artifact_id="art_editme01",
            content="x" * (25 * 1024 * 1024 + 1),
            base_hash=_sha("# old\n"),
        )
    assert env["entry"].read_text(encoding="utf-8") == "# old\n"


async def test_wrong_agent_id_is_not_found(env):
    from narranexus.platform.artifact._artifact_impl.errors import ArtifactNotFound
    svc: ArtifactService = env["service"]
    with pytest.raises(ArtifactNotFound):
        await svc.save_user_content(
            agent_id="agent_other",
            artifact_id="art_editme01",
            content="hijack",
            base_hash=_sha("# old\n"),
        )


async def test_atomic_write_leaves_no_temp_droppings(env):
    svc: ArtifactService = env["service"]
    await svc.save_user_content(
        agent_id="agent_x",
        artifact_id="art_editme01",
        content="# new\n",
        base_hash=_sha("# old\n"),
    )
    siblings = sorted(os.listdir(env["workspace"]))
    assert siblings == ["notes.md"]


def test_editable_kinds_pinned_to_frontend_registry():
    """Cross-language consistency pin (review #334 I18): the frontend's
    kindRegistry declares which kinds get an editing surface
    (editSurface not none/office-watch) and pins the SAME literal set in
    kindRegistry.test.ts. A kind added on one side without the other shows
    as an editor whose saves always 400 — this test and that one must move
    together."""
    from narranexus.platform.artifact._artifact_impl.user_edit import EDITABLE_KINDS

    assert set(EDITABLE_KINDS) == {"text/markdown", "text/csv", "text/html"}
