"""
@file_name: test_team_artifact_view_token.py
@author: NarraNexus
@date: 2026-08-07
@description: Viewing a team artifact's raw content.

The existing mint route requires the caller's agent to BE the artifact's
agent, which is exactly wrong for a team: the team panel shows work produced
by several members, and a teammate opening a colleague's artifact is the
normal case, not an attack.

The token payload is deliberately unchanged. Authorisation happens at MINT —
that is where the existing design already puts it — so the team check lives in
the team route and the token keeps carrying the PRODUCER's agent_id, which is
what the raw serving path resolves against. Nothing downstream has to learn
about teams.

What must not change: the artifact still has to be in THAT team. A token
minted through a team the caller owns must not reach another team's artifact,
nor a private one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.routes.artifacts._token import verify
from narranexus_plugins.teams.routes import _authorize_team_artifact
from fastapi import HTTPException
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema.artifact_schema import Artifact

OWNER = "user_t"
TID = "team_abc"
PRODUCER = "agent_producer"


async def _seed(db, artifact_id, *, team_id, agent_id=PRODUCER):
    now = datetime.now(timezone.utc)
    await ArtifactRepository(db).create(Artifact(
        artifact_id=artifact_id, agent_id=agent_id, user_id=OWNER, session_id=None,
        title=artifact_id, kind="text/html", pinned=True, team_id=team_id,
        file_path=f"p/{artifact_id}/index.html", size_bytes=1,
        created_at=now, updated_at=now,
    ))


@pytest.mark.asyncio
async def test_team_artifact_authorises_and_keeps_the_producer(db_client):
    """The teammate opening it is not the producer — that is the whole point."""
    await _seed(db_client, "art_team", team_id=TID)

    art = await _authorize_team_artifact(db_client, TID, "art_team")
    assert art.agent_id == PRODUCER, (
        "the token must be minted for the PRODUCER so raw serving resolves"
    )


@pytest.mark.asyncio
async def test_another_teams_artifact_is_refused(db_client):
    await _seed(db_client, "art_other", team_id="team_zzz")

    with pytest.raises(HTTPException) as e:
        await _authorize_team_artifact(db_client, TID, "art_other")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_a_private_artifact_is_not_reachable_through_a_team(db_client):
    """Owning a team is not a way into the owner's private work."""
    await _seed(db_client, "art_private", team_id=None)

    with pytest.raises(HTTPException) as e:
        await _authorize_team_artifact(db_client, TID, "art_private")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_artifact_is_404_not_403(db_client):
    """404 rather than 403, matching the agent route: a different status would
    let a prober map which artifact ids exist."""
    with pytest.raises(HTTPException) as e:
        await _authorize_team_artifact(db_client, TID, "art_nope")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_minted_token_verifies_with_the_producer_identity(db_client):
    """End of the chain: the token the team route mints is the same shape the
    public raw route already knows how to verify."""
    from backend.routes.artifacts import _token as artifact_token

    await _seed(db_client, "art_team", team_id=TID)
    art = await _authorize_team_artifact(db_client, TID, "art_team")

    claims = verify(artifact_token.mint(agent_id=art.agent_id, artifact_id=art.artifact_id))
    assert claims.agent_id == PRODUCER
    assert claims.artifact_id == "art_team"
