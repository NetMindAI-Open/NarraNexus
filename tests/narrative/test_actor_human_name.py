"""
@file_name: test_actor_human_name.py
@author: NarraNexus
@date: 2026-06-12
@description: The narrative main prompt must render USER / PARTICIPANT actors
by HUMAN display name, not the opaque user_id. AGENT actors keep their
agent_id (a non-human key).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from xyz_agent_context.narrative.models import (
    Narrative, NarrativeInfo, NarrativeActor, NarrativeActorType, NarrativeType,
)
from xyz_agent_context.narrative._narrative_impl.prompt_builder import PromptBuilder


def _narrative(actors):
    now = datetime(2026, 6, 12, 0, 0, 0)
    return Narrative(
        id="narr_1",
        type=NarrativeType.CHAT,
        agent_id="agent_x",
        narrative_info=NarrativeInfo(
            name="N", description="D", current_summary="S", actors=actors,
        ),
        event_ids=[],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_user_actor_rendered_as_name_agent_kept_as_id(db_client, monkeypatch):
    import xyz_agent_context.utils.db_factory as dbf
    async def _fake_db():
        return db_client
    monkeypatch.setattr(dbf, "get_db_client", _fake_db)

    await db_client.insert("users", {
        "user_id": "owner_hex", "display_name": "Alice",
        "user_type": "individual", "status": "active",
    })

    narrative = _narrative([
        NarrativeActor(id="owner_hex", type=NarrativeActorType.USER),
        NarrativeActor(id="agent_x", type=NarrativeActorType.AGENT),
    ])
    prompt = await PromptBuilder.build_main_prompt(narrative)

    assert "Alice (user)" in prompt          # human actor by name
    assert "owner_hex (user)" not in prompt   # not the hex
    assert "agent_x (agent)" in prompt        # agent id kept as-is
