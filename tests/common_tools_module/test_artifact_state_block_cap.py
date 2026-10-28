"""
@file_name: test_artifact_state_block_cap.py
@author: NarraNexus
@date: 2026-08-07
@description: The 'Your registered artifacts' block is capped.

The block used to list EVERY pinned artifact an agent owned, with no limit
and no ordering, on EVERY turn — so an agent that keeps registering grows
its own system prompt without bound (artifacts have no quota). Capping it
to the most recently updated N is the fix; the full set stays reachable
through the artifact tooling, so nothing is lost, it is just not all
carried in-context every turn.

Ordering by updated_at is what makes the cap self-correcting: re-registering
an artifact (the update path) refreshes the timestamp, so whatever the agent
is actively iterating on stays listed and only genuinely cold entries fall
off.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from narranexus_plugins.common_tools_module.common_tools_module import (
    ARTIFACT_STATE_BLOCK_LIMIT,
    CommonToolsModule,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


async def _seed(repo, artifact_id, *, age_minutes: int):
    """Seed one pinned artifact whose updated_at is `age_minutes` old."""
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    await repo.create(Artifact(
        artifact_id=artifact_id, agent_id="agent_x", user_id="user_y",
        session_id=None, title=f"T-{artifact_id}", kind="text/markdown",
        pinned=True, file_path=f"{WS_REL}/{artifact_id}.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)
    repo = ArtifactRepository(db_client)
    mod = CommonToolsModule("agent_x", "user_y", db_client)
    yield {"repo": repo, "mod": mod}


@pytest.mark.asyncio
async def test_block_lists_at_most_the_cap(env):
    """More artifacts than the cap → only the cap's worth are listed."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    listed = [f"art_{i:03d}" for i in range(over) if f"art_{i:03d}" in block]
    assert len(listed) == ARTIFACT_STATE_BLOCK_LIMIT, (
        f"block must carry at most {ARTIFACT_STATE_BLOCK_LIMIT} artifacts, got {len(listed)}"
    )


@pytest.mark.asyncio
async def test_block_keeps_the_freshest_not_an_arbitrary_slice(env):
    """The survivors must be the most recently updated ones — that is what
    makes the cap self-correcting for whatever the agent is working on."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):  # i == age in minutes → art_000 is the freshest
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    assert "art_000" in block, "the freshest artifact must always be listed"
    oldest = f"art_{over - 1:03d}"
    assert oldest not in block, "the coldest artifact must be the one dropped"


@pytest.mark.asyncio
async def test_under_the_cap_lists_everything(env):
    """The common case is unchanged: below the cap, nothing is hidden."""
    for i in range(3):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    for i in range(3):
        assert f"art_{i:03d}" in block


@pytest.mark.asyncio
async def test_repository_default_is_still_unlimited(env):
    """`list_pinned()` without a limit must stay exhaustive — bootstrap's
    duplicate check (bootstrap/profiles.py) scans it and would silently
    re-create profile artifacts if the repository started truncating."""
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    assert len(await env["repo"].list_pinned("agent_x")) == over


# ── the block spans the agent's teams ──────────────────────────────────────


@pytest.mark.asyncio
async def test_block_shows_teammates_artifact_from_a_shared_team(env, db_client):
    """End-to-end guard for the reverse leak: the block is where an agent
    learns a teammate's artifact exists at all. If it only listed the agent's
    own rows, hand-off would silently stop working — nothing would raise, the
    agent would just never mention the artifact again.
    """
    await db_client.insert("team_members", {"team_id": "team_1", "agent_id": "agent_x"})
    now = datetime.now(timezone.utc)
    await env["repo"].create(Artifact(
        artifact_id="art_teammate", agent_id="agent_other", user_id="user_y",
        session_id=None, title="Teammate report", kind="text/markdown",
        pinned=True, team_id="team_1", file_path=f"{WS_REL}/teammate.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))

    block = await env["mod"]._render_artifact_state_block()
    assert "art_teammate" in block


@pytest.mark.asyncio
async def test_block_hides_a_team_the_agent_does_not_belong_to(env, db_client):
    """Membership is the boundary. Same owning user, different team → not
    visible, or one team would read another's workspace."""
    now = datetime.now(timezone.utc)
    await env["repo"].create(Artifact(
        artifact_id="art_foreign", agent_id="agent_other", user_id="user_y",
        session_id=None, title="Other team", kind="text/markdown",
        pinned=True, team_id="team_zzz", file_path=f"{WS_REL}/foreign.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))

    block = await env["mod"]._render_artifact_state_block()
    assert "art_foreign" not in block


# ── paths the agent can actually act on ───────────────────────────────────
#
# The block strips the CALLING agent's own workspace prefix and renders the
# remainder, which the trailing instruction and the agent's tools both read as
# a workspace-relative path. For a teammate's team artifact neither prefix
# matches, so the base-relative path was printed verbatim — and a relative path
# resolves against the reader's OWN workspace (NexusPower's confinement layer
# rebases relative paths there by design), producing a file that does not
# exist. "See the list" worked; the first step of picking the work up did not.


@pytest.mark.asyncio
async def test_a_teammates_artifact_gets_an_absolute_path(env, db_client):
    """Cross-workspace entries must render absolutely: there is no relative
    form of "inside another agent's workspace" that this agent can open."""
    await db_client.insert("team_members", {"team_id": "team_1", "agent_id": "agent_x"})
    now = datetime.now(timezone.utc)
    mate_rel = agent_workspace_relpath("agent_other", "user_y")
    await env["repo"].create(Artifact(
        artifact_id="art_mate", agent_id="agent_other", user_id="user_y",
        session_id=None, title="Mate report", kind="text/markdown", pinned=True,
        team_id="team_1", file_path=f"{mate_rel}/report.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))

    block = await env["mod"]._render_artifact_state_block()

    from narranexus.platform.settings import settings as sa
    expected = os.path.join(os.path.realpath(sa.base_working_path), mate_rel, "report.md")
    assert expected in block, (
        "a teammate's artifact must be named by a path this agent can open"
    )
    assert f"`{mate_rel}/report.md`" not in block, (
        "the bare base-relative form would resolve against the reader's own "
        "workspace and miss"
    )


@pytest.mark.asyncio
async def test_an_artifact_in_the_team_folder_gets_an_absolute_path(env, db_client):
    """Same for entries registered straight out of the shared folder: they sit
    outside every agent workspace by design."""
    await db_client.insert("team_members", {"team_id": "team_1", "agent_id": "agent_x"})
    now = datetime.now(timezone.utc)
    await env["repo"].create(Artifact(
        artifact_id="art_shared", agent_id="agent_other", user_id="user_y",
        session_id=None, title="Shared", kind="text/markdown", pinned=True,
        team_id="team_1", file_path="user_y/_shared/teams/team_1/plan.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))

    block = await env["mod"]._render_artifact_state_block()

    from narranexus.platform.settings import settings as sa
    expected = os.path.join(
        os.path.realpath(sa.base_working_path), "user_y/_shared/teams/team_1/plan.md"
    )
    assert expected in block


@pytest.mark.asyncio
async def test_own_artifacts_stay_workspace_relative(env):
    """The common case is unchanged: the agent's own files keep the short
    relative form its tools take without ceremony."""
    await _seed(env["repo"], "art_mine", age_minutes=1)

    block = await env["mod"]._render_artifact_state_block()
    assert "`art_mine.md`" in block
    assert WS_REL not in block, "own entries must not become absolute"


@pytest.mark.asyncio
async def test_the_instruction_explains_the_absolute_form(env, db_client):
    """Rendering an absolute path is only half the fix: the agent also has to
    be told these exist, or it will keep reading every entry as relative."""
    await db_client.insert("team_members", {"team_id": "team_1", "agent_id": "agent_x"})
    now = datetime.now(timezone.utc)
    await env["repo"].create(Artifact(
        artifact_id="art_mate", agent_id="agent_other", user_id="user_y",
        session_id=None, title="Mate", kind="text/markdown", pinned=True,
        team_id="team_1", file_path=f"{agent_workspace_relpath('agent_other', 'user_y')}/r.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))

    block = await env["mod"]._render_artifact_state_block()
    assert "absolute" in block.lower()


# ---------------------------------------------------------------------------
# Truthful footer (2026-08-18, spec artifact-events §4): the cap must never be
# silent — past the limit the block says how many more exist and names the
# tool that lists them, so the agent knows it is looking at a window and
# queries before concluding an artifact does not exist.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_block_under_cap_has_no_footer(env):
    for i in range(3):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    assert "list_artifacts" not in block
    assert "more" not in block


@pytest.mark.asyncio
async def test_block_over_cap_says_how_many_more_and_names_the_tool(env):
    over = ARTIFACT_STATE_BLOCK_LIMIT + 5
    for i in range(over):
        await _seed(env["repo"], f"art_{i:03d}", age_minutes=i)

    block = await env["mod"]._render_artifact_state_block()
    # The exact copy may evolve; the contract is: total count + tool name.
    assert f"{over}" in block
    assert "list_artifacts" in block
