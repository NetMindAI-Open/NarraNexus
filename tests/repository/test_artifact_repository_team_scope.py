"""
@file_name: test_artifact_repository_team_scope.py
@author: NarraNexus
@date: 2026-08-07
@description: The three artifact surfaces, and the leaks between them.

Artifacts gained a team dimension, which splits one list into three with
DIFFERENT answers — and the failure modes point in opposite directions, so
testing only one of them proves nothing:

  private panel   private only          leak = a team's work shows up in a
                                        one-to-one chat
  team panel      that team only        leak = one team sees another's work
  agent prompt    private ∪ its teams   leak = the agent cannot see the work
                                        it is supposed to pick up, and the
                                        hand-off silently stops working

The last one is the reverse leak: an over-narrow query fails closed and looks
harmless, but it breaks the entire point of a shared workspace. Both
directions are asserted below.

Membership is resolved from `team_members`, never from the owning user: one
user owns many teams, so "same user" would hand every team's artifacts to
every agent the user owns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact

USER = "user_1"


async def _seed_artifact(
    repo, artifact_id, *, agent_id="agent_a", team_id=None,
    pinned=True, session_id=None, age_minutes=0,
):
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    await repo.create(Artifact(
        artifact_id=artifact_id, agent_id=agent_id, user_id=USER,
        session_id=session_id, title=artifact_id, kind="text/markdown",
        pinned=pinned, team_id=team_id, file_path=f"p/{artifact_id}.md",
        size_bytes=1, created_at=now, updated_at=now,
    ))


async def _join_team(db, agent_id, team_id):
    await db.insert("team_members", {"team_id": team_id, "agent_id": agent_id})


@pytest.fixture
async def repo(db_client):
    return ArtifactRepository(db_client)


# ── forward leak: team work must not surface in the private surfaces ───────


@pytest.mark.asyncio
async def test_pinned_list_excludes_team_artifacts(repo):
    await _seed_artifact(repo, "art_private")
    await _seed_artifact(repo, "art_team", team_id="team_1")

    ids = {a.artifact_id for a in await repo.list_pinned("agent_a")}
    assert ids == {"art_private"}


@pytest.mark.asyncio
async def test_session_list_excludes_team_artifacts(repo):
    await _seed_artifact(repo, "art_sess", pinned=False, session_id="s1")
    await _seed_artifact(repo, "art_team", pinned=False, session_id="s1", team_id="team_1")

    ids = {a.artifact_id for a in await repo.list_by_session("agent_a", "s1")}
    assert ids == {"art_sess"}


# ── the team surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_list_returns_only_that_team(repo):
    await _seed_artifact(repo, "art_t1", team_id="team_1")
    await _seed_artifact(repo, "art_t2", team_id="team_2")
    await _seed_artifact(repo, "art_private")

    ids = {a.artifact_id for a in await repo.list_by_team("team_1")}
    assert ids == {"art_t1"}


@pytest.mark.asyncio
async def test_team_list_is_freshest_first(repo):
    await _seed_artifact(repo, "art_old", team_id="team_1", age_minutes=60)
    await _seed_artifact(repo, "art_new", team_id="team_1", age_minutes=1)

    got = [a.artifact_id for a in await repo.list_by_team("team_1")]
    assert got[0] == "art_new"


@pytest.mark.asyncio
async def test_team_list_includes_every_member_agents_work(repo):
    """The panel is the TEAM's, not one agent's: whoever produced it, it
    belongs to the team workspace."""
    await _seed_artifact(repo, "art_by_a", agent_id="agent_a", team_id="team_1")
    await _seed_artifact(repo, "art_by_b", agent_id="agent_b", team_id="team_1")

    ids = {a.artifact_id for a in await repo.list_by_team("team_1")}
    assert ids == {"art_by_a", "art_by_b"}


# ── reverse leak: the agent's own context must span its teams ─────────────


@pytest.mark.asyncio
async def test_agent_context_unions_private_and_team(repo, db_client):
    await _join_team(db_client, "agent_a", "team_1")
    await _seed_artifact(repo, "art_private")
    await _seed_artifact(repo, "art_team", agent_id="agent_b", team_id="team_1")

    ids = {a.artifact_id for a in await repo.list_for_agent_context("agent_a")}
    assert ids == {"art_private", "art_team"}, (
        "the agent must see its teams' artifacts or it cannot pick up a "
        "teammate's work — the reverse leak"
    )


@pytest.mark.asyncio
async def test_agent_context_spans_every_team_the_agent_is_in(repo, db_client):
    """An agent can belong to several teams; taking only one is the same
    failure as taking none, just harder to notice."""
    await _join_team(db_client, "agent_a", "team_1")
    await _join_team(db_client, "agent_a", "team_2")
    await _seed_artifact(repo, "art_t1", agent_id="agent_b", team_id="team_1")
    await _seed_artifact(repo, "art_t2", agent_id="agent_c", team_id="team_2")

    ids = {a.artifact_id for a in await repo.list_for_agent_context("agent_a")}
    assert ids == {"art_t1", "art_t2"}


# ── cross-team: membership is the boundary, not ownership ─────────────────


@pytest.mark.asyncio
async def test_agent_context_excludes_teams_the_agent_is_not_in(repo, db_client):
    """All these teams belong to the same user. If the union were keyed on
    the owner instead of membership, a non-member agent would read another
    team's workspace."""
    await _join_team(db_client, "agent_a", "team_1")
    await _seed_artifact(repo, "art_mine", agent_id="agent_b", team_id="team_1")
    await _seed_artifact(repo, "art_foreign", agent_id="agent_c", team_id="team_2")

    ids = {a.artifact_id for a in await repo.list_for_agent_context("agent_a")}
    assert "art_foreign" not in ids
    assert "art_mine" in ids


@pytest.mark.asyncio
async def test_agent_context_excludes_other_agents_private_work(repo, db_client):
    await _join_team(db_client, "agent_a", "team_1")
    await _seed_artifact(repo, "art_other_private", agent_id="agent_b")

    ids = {a.artifact_id for a in await repo.list_for_agent_context("agent_a")}
    assert ids == set()


# ── ordering / cap ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_context_honours_limit_freshest_first(repo, db_client):
    await _join_team(db_client, "agent_a", "team_1")
    await _seed_artifact(repo, "art_cold", age_minutes=90)
    await _seed_artifact(repo, "art_warm", team_id="team_1", agent_id="agent_b", age_minutes=30)
    await _seed_artifact(repo, "art_hot", age_minutes=1)

    got = [a.artifact_id for a in await repo.list_for_agent_context("agent_a", limit=2)]
    assert got == ["art_hot", "art_warm"]


# ── the management surface is deliberately unchanged ──────────────────────


@pytest.mark.asyncio
async def test_user_management_list_still_sees_everything(repo, db_client):
    """Settings → Artifacts manages everything the user owns. Team artifacts
    are owned by that same user, so this surface intentionally keeps no team
    filter — narrowing it would hide files the user is accountable for."""
    await _join_team(db_client, "agent_a", "team_1")
    await _seed_artifact(repo, "art_private")
    await _seed_artifact(repo, "art_team", team_id="team_1")

    ids = {a.artifact_id for a in await repo.list_by_user(USER)}
    assert ids == {"art_private", "art_team"}
