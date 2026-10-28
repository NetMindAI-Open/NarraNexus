"""
@file_name: test_state_block_freshness.py
@date: 2026-08-19
@description: The state block is trigger point T-A of external-edit safety
(spec B §2.3): rendering it stats each listed artifact, commits confirmed
external changes, and marks lines so the agent KNOWS the file moved past its
last commit point before it edits blind.

Markers derive from the LAST history action (stateless):
  external_edited → "EXTERNALLY MODIFIED"
  user_edited     → "modified by the user"
  agent re-register (updated/registered) clears the marker.
Office kinds additionally flag a desktop-Office ~$ lock.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from narranexus_plugins.common_tools_module.common_tools_module import CommonToolsModule
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    repo = ArtifactRepository(db_client)
    mod = CommonToolsModule("agent_x", "user_y", db_client)
    yield {"base": base, "repo": repo, "mod": mod, "db": db_client}


async def _seed(env, aid, fname, content, kind="text/markdown"):
    entry = env["base"] / WS_REL / fname
    entry.write_text(content, encoding="utf-8")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await env["repo"].create(Artifact(
        artifact_id=aid, agent_id="agent_x", user_id="user_y",
        session_id=None, title=fname, kind=kind, pinned=True,
        file_path=f"{WS_REL}/{fname}", size_bytes=len(content),
        content_hash=_sha(content), created_at=past, updated_at=past,
    ))
    return entry


@pytest.mark.asyncio
async def test_untouched_artifact_has_no_marker(env):
    await _seed(env, "art_clean001", "clean.md", "# same\n")
    block = await env["mod"]._render_artifact_state_block()
    assert "art_clean001" in block
    assert "EXTERNALLY MODIFIED" not in block


@pytest.mark.asyncio
async def test_external_change_is_detected_and_marked(env):
    entry = await _seed(env, "art_ext00001", "ext.md", "# v1\n")
    entry.write_text("# v2 changed outside\n", encoding="utf-8")

    block = await env["mod"]._render_artifact_state_block()
    assert "EXTERNALLY MODIFIED" in block
    # the detection also committed: row hash refreshed
    row = await env["repo"].get_by_id("art_ext00001")
    assert row.content_hash == _sha("# v2 changed outside\n")


@pytest.mark.asyncio
async def test_user_edit_marker_from_history(env):
    await _seed(env, "art_used0001", "used.md", "# same\n")
    from narranexus.platform.repository.team_workspace_repository import (
        ArtifactHistoryRepository,
    )
    await ArtifactHistoryRepository(env["db"]).append(
        artifact_id="art_used0001", agent_id="agent_x",
        file_path=f"{WS_REL}/used.md", size_bytes=7, action="user_edited",
    )
    block = await env["mod"]._render_artifact_state_block()
    assert "modified by the user" in block


@pytest.mark.asyncio
async def test_re_register_clears_the_marker(env):
    await _seed(env, "art_cleared1", "cleared.md", "# same\n")
    from narranexus.platform.repository.team_workspace_repository import (
        ArtifactHistoryRepository,
    )
    hist = ArtifactHistoryRepository(env["db"])
    await hist.append(
        artifact_id="art_cleared1", agent_id="agent_x",
        file_path=f"{WS_REL}/cleared.md", size_bytes=7, action="user_edited",
    )
    # agent re-registered afterwards — its refresh flow — marker must clear
    await hist.append(
        artifact_id="art_cleared1", agent_id="agent_x",
        file_path=f"{WS_REL}/cleared.md", size_bytes=7, action="updated",
    )
    block = await env["mod"]._render_artifact_state_block()
    assert "modified by the user" not in block


@pytest.mark.asyncio
async def test_office_lock_is_flagged(env):
    await _seed(
        env, "art_office01", "deck.pptx", "pk-fake",
        kind="application/vnd.officecli-live",
    )
    (env["base"] / WS_REL / "~$deck.pptx").write_bytes(b"lock")
    block = await env["mod"]._render_artifact_state_block()
    assert "desktop Office" in block


@pytest.mark.asyncio
async def test_office_lock_probe_does_not_follow_a_link_into_another_tenant(env):
    """The artifact's folder was swapped for a link to another tenant's folder
    holding an open document. The lock probe must not look there: no note, and
    the freshness pass must not commit the victim's hash onto our row."""
    import os

    victim = env["base"] / agent_workspace_relpath("agent_v", "user_v") / "docs"
    victim.mkdir(parents=True)
    (victim / "deck.pptx").write_bytes(b"victim-deck")
    (victim / "~$deck.pptx").write_bytes(b"lock")
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await env["repo"].create(Artifact(
        artifact_id="art_office02", agent_id="agent_x", user_id="user_y",
        session_id=None, title="deck", kind="application/vnd.officecli-live",
        pinned=True, file_path=f"{WS_REL}/docs/deck.pptx", size_bytes=7,
        content_hash=_sha("pk-fake"), created_at=past, updated_at=past,
    ))
    os.symlink(victim, env["base"] / WS_REL / "docs")

    block = await env["mod"]._render_artifact_state_block()
    assert "art_office02" in block
    assert "desktop Office" not in block
    assert "EXTERNALLY MODIFIED" not in block
    row = await env["repo"].get_by_id("art_office02")
    assert row.content_hash == _sha("pk-fake")
