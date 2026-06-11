"""
@file_name: test_narrative_user_filter_recall.py
@author: Bin Liang
@date: 2026-05-29
@description: F2 regression — paged user-filter scan must not lose recall.

NarrativeRepository.get_by_agent_user stores user_id
inside the narrative_info JSON actors, so the user filter is applied in
Python. The old implementation fetched only `limit*2` newest rows then
filtered, which silently dropped a user's narratives once the agent
accumulated more than limit*2 narratives across all its users. These
tests pin the new paged-scan behavior: the target user's narratives are
seeded as the OLDEST rows (well outside any limit*2 window) and must
still be returned.
"""
from datetime import datetime, timezone, timedelta

import pytest

from xyz_agent_context.repository import NarrativeRepository
from xyz_agent_context.narrative.models import (
    Narrative,
    NarrativeType,
    NarrativeInfo,
    NarrativeActor,
    NarrativeActorType,
)


def _make_narrative(nid: str, agent_id: str, user_id: str, updated_at: datetime) -> Narrative:
    return Narrative(
        id=nid,
        type=NarrativeType.CHAT,
        agent_id=agent_id,
        narrative_info=NarrativeInfo(
            name=nid,
            description="",
            current_summary="",
            actors=[NarrativeActor(id=user_id, type=NarrativeActorType.USER)],
        ),
        event_ids=[],
        created_at=updated_at,
        updated_at=updated_at,
    )


@pytest.fixture
def repo(db_client):
    return NarrativeRepository(db_client)


@pytest.mark.asyncio
async def test_get_by_agent_user_finds_user_beyond_limit2_window(repo):
    agent = "agent_F2"
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)

    # 50 newest narratives belong to OTHER users (would fill a limit*2 window).
    for i in range(50):
        await repo.save(_make_narrative(
            f"nar_other_{i:03d}", agent, f"other_user_{i}",
            base + timedelta(hours=100 + i),
        ))
    # 3 OLDEST narratives belong to the target user.
    for i in range(3):
        await repo.save(_make_narrative(
            f"nar_target_{i}", agent, "target_user",
            base + timedelta(hours=i),
        ))

    # Old impl (fetch limit*2=10 newest, filter) would return 0 here.
    found = await repo.get_by_agent_user(agent, "target_user", limit=5)
    ids = {n.id for n in found}
    assert ids == {"nar_target_0", "nar_target_1", "nar_target_2"}


@pytest.mark.asyncio
async def test_get_by_agent_user_respects_limit(repo):
    agent = "agent_F2b"
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(10):
        await repo.save(_make_narrative(
            f"nar_u_{i:02d}", agent, "u1", base + timedelta(hours=i),
        ))
    found = await repo.get_by_agent_user(agent, "u1", limit=4)
    # Respects the limit and returns matches (recall + cap are the contract;
    # ordering is delegated to the DB's updated_at sort and not re-asserted
    # here since it's unchanged from the pre-F2 behavior).
    assert len(found) == 4
    assert all(n.id.startswith("nar_u_") for n in found)
    assert len({n.id for n in found}) == 4

