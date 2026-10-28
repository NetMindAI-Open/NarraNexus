"""
@file_name: test_heal_team_artifact.py
@author: NarraNexus
@date: 2026-08-10
@description: Recovering a broken pointer on a TEAM artifact.

Every step of heal was written when an artifact could only live in the
producing agent's workspace, and all three broke once team artifacts were
required to live in the team folder:

  1. the "pointer is already valid" shortcut compares against the workspace
     root, which a team artifact never satisfies — so an artifact whose file is
     perfectly fine is declared broken and the flow continues;
  2. the candidate scan walks that same workspace, so the modal offers the
     agent's own unrelated files for a team artifact;
  3. both re-registration paths omit team_id, so they hit the ownership check
     added with the reachability guard and fail with "artifact not found".

The net effect was a recovery flow that could not succeed: intact content
reported as broken, a candidate list drawn from the wrong directory, and every
choice ending in an error. This was cited in teams.py as the reason clear_files
must cascade — true, but a reason to fix the flow, not to leave it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from narranexus.platform.artifact import ArtifactService
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.workspace_paths import (
    agent_workspace_path,
    team_shared_dir,
)

AGENT = "agent_a"
USER = "user_1"
TEAM = "team_1"


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "ws"
    from narranexus.platform.settings import settings as sa

    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    ws = agent_workspace_path(AGENT, USER, base=str(base))
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "unrelated.md").write_text("private\n")

    shared = team_shared_dir(USER, TEAM, str(base))
    shared.mkdir(parents=True, exist_ok=True)

    yield {
        "svc": ArtifactService(db_client),
        "repo": ArtifactRepository(db_client),
        "ws": ws,
        "shared": shared,
        "base": Path(str(base)),
    }


async def _seed(env, *, rel_path):
    now = datetime.now(timezone.utc)
    await env["repo"].create(
        Artifact(
            artifact_id="art_team",
            agent_id=AGENT,
            user_id=USER,
            session_id=None,
            title="T",
            kind="text/markdown",
            pinned=True,
            team_id=TEAM,
            file_path=rel_path,
            size_bytes=1,
            created_at=now,
            updated_at=now,
        )
    )


@pytest.mark.asyncio
async def test_an_intact_team_pointer_is_recognised_as_valid(env):
    """Step 1 must look at the folder the artifact actually lives in."""
    (env["shared"] / "report.md").write_text("still here\n")
    rel = str((env["shared"] / "report.md").relative_to(env["base"]))
    await _seed(env, rel_path=rel)

    res = await env["svc"].heal(agent_id=AGENT, user_id=USER, artifact_id="art_team")
    assert res.recovered is True
    assert "already valid" in res.message


@pytest.mark.asyncio
async def test_candidates_come_from_the_team_folder(env):
    """Step 3 must not offer the agent's private files as replacements for a
    team artifact — they are in a directory the team cannot even read."""
    (env["shared"] / "candidate.md").write_text("ours\n")
    await _seed(env, rel_path="nonexistent/gone.md")

    res = await env["svc"].heal(agent_id=AGENT, user_id=USER, artifact_id="art_team")

    offered = [c.workspace_path for c in (res.candidates or [])]
    assert not any("unrelated.md" in c for c in offered), (
        f"private workspace files offered for a team artifact: {offered}"
    )


@pytest.mark.asyncio
async def test_auto_recovery_keeps_the_artifact_in_its_team(env):
    """A single candidate is re-registered automatically; that path must pass
    team_id or it trips the ownership check and reports "not found"."""
    (env["shared"] / "only.md").write_text("ours\n")
    await _seed(env, rel_path="nonexistent/gone.md")

    res = await env["svc"].heal(agent_id=AGENT, user_id=USER, artifact_id="art_team")

    assert res.recovered is True, res.message
    assert (await env["repo"].get_by_id("art_team")).team_id == TEAM


@pytest.mark.asyncio
async def test_a_user_picked_path_recovers_a_team_artifact(env):
    """Step 2 — the modal's explicit choice — has the same requirement."""
    (env["shared"] / "picked.md").write_text("ours\n")
    (env["shared"] / "other.md").write_text("also ours\n")
    await _seed(env, rel_path="nonexistent/gone.md")

    res = await env["svc"].heal(
        agent_id=AGENT,
        user_id=USER,
        artifact_id="art_team",
        entry_path=str(env["shared"] / "picked.md"),
    )
    assert res.recovered is True
    healed = await env["repo"].get_by_id("art_team")
    assert healed.team_id == TEAM
    assert healed.file_path.endswith("picked.md")


@pytest.mark.asyncio
async def test_private_artifacts_heal_exactly_as_before(env):
    """The private path is the one that already worked; it must not move."""
    (env["ws"] / "mine.md").write_text("mine\n")
    rel = str((env["ws"] / "mine.md").relative_to(env["base"]))
    now = datetime.now(timezone.utc)
    await env["repo"].create(
        Artifact(
            artifact_id="art_priv",
            agent_id=AGENT,
            user_id=USER,
            session_id=None,
            title="P",
            kind="text/markdown",
            pinned=True,
            team_id=None,
            file_path=rel,
            size_bytes=1,
            created_at=now,
            updated_at=now,
        )
    )

    res = await env["svc"].heal(agent_id=AGENT, user_id=USER, artifact_id="art_priv")
    assert res.recovered is True
    assert "already valid" in res.message


@pytest.mark.asyncio
async def test_a_team_folder_swapped_for_a_link_is_not_scanned(env, tmp_path):
    """The team folder lives in the owner's mount; swapped for a link to another
    tenant's team folder, heal offers nothing from it."""
    import os
    import shutil

    victim_team = team_shared_dir("user_victim", TEAM, str(env["base"]))
    victim_team.mkdir(parents=True)
    (victim_team / "plan.md").write_text("victim plan\n")
    shutil.rmtree(env["shared"])
    os.symlink(victim_team, env["shared"])
    await _seed(env, rel_path="nonexistent/gone.md")

    res = await env["svc"].heal(agent_id=AGENT, user_id=USER, artifact_id="art_team")
    assert res.recovered is False
    assert res.candidates == []
