"""
@file_name: test_patrol_speech_limits.py
@author:
@date: 2026-08-10
@description: Patrol speaks outside the cascade cap — so its own limit is the
only backstop left.

Owner decision 2026-08-07 (option a): a patrol message is the PLATFORM taking
stock, not an agent joining the conversation, so it neither pushes the cascade
depth up nor has its @mentions stripped.

That decision is load-bearing in both directions:

* without the exemption, patrol fails exactly where it is needed. The broken
  flow IS a long unbroken run of agent messages with no user in between, so
  depth is already at the cap and the chase @ would be stripped — silently.
* with the exemption, the runaway-@ protection no longer covers patrol, and
  the only thing standing between a confused model and a flooded room is the
  counter tested here. Which is why it lives in the DB: the bus's existing
  limiter is an in-memory dict keyed on `time.monotonic()`, and a backstop a
  restart erases is not a backstop.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.patrol import (
    PATROL_SPEECH_MAX,
    PATROL_SPEECH_WINDOW_S,
    may_patrol_speak,
    note_patrol_spoke,
)
from narranexus.platform.utils.timezone import utc_now


async def _team(db, team_id="t1", *, spoke_at=None, count=0):
    row = {"team_id": team_id, "owner_user_id": "usr_1", "name": "T",
           "patrol_spoke_count": count}
    if spoke_at is not None:
        row["patrol_spoke_at"] = spoke_at
    await db.insert("teams", row)


@pytest.mark.asyncio
async def test_a_quiet_team_may_speak(db_client):
    await _team(db_client)
    assert await may_patrol_speak(db_client, "t1") is True


@pytest.mark.asyncio
async def test_the_cap_holds_within_the_window(db_client):
    await _team(db_client, spoke_at=utc_now(), count=PATROL_SPEECH_MAX)

    assert await may_patrol_speak(db_client, "t1") is False


@pytest.mark.asyncio
async def test_the_window_rolls_over(db_client):
    """Past the window the count resets — a cap is not a permanent gag."""
    stale = utc_now() - timedelta(seconds=PATROL_SPEECH_WINDOW_S + 60)
    await _team(db_client, spoke_at=stale, count=PATROL_SPEECH_MAX)

    assert await may_patrol_speak(db_client, "t1") is True


@pytest.mark.asyncio
async def test_speaking_is_counted(db_client):
    await _team(db_client)

    await note_patrol_spoke(db_client, "t1")
    await note_patrol_spoke(db_client, "t1")

    row = await db_client.get_one("teams", {"team_id": "t1"})
    assert row["patrol_spoke_count"] == 2
    assert row["patrol_spoke_at"]


@pytest.mark.asyncio
async def test_the_count_survives_a_restart(db_client):
    """The point of putting it on disk.

    The bus's in-memory limiter would read zero here, and a model stuck in a
    chase loop would get a fresh budget every time workers restarted.
    """
    await _team(db_client, spoke_at=utc_now(), count=PATROL_SPEECH_MAX - 1)

    await note_patrol_spoke(db_client, "t1")

    # A "new process" is just another read of the same row.
    assert await may_patrol_speak(db_client, "t1") is False


@pytest.mark.asyncio
async def test_a_new_window_restarts_the_count_at_one(db_client):
    stale = utc_now() - timedelta(seconds=PATROL_SPEECH_WINDOW_S + 60)
    await _team(db_client, spoke_at=stale, count=PATROL_SPEECH_MAX)

    await note_patrol_spoke(db_client, "t1")

    row = await db_client.get_one("teams", {"team_id": "t1"})
    assert row["patrol_spoke_count"] == 1


@pytest.mark.asyncio
async def test_an_unreadable_team_is_allowed_to_speak(db_client):
    """Fail OPEN: patrol's whole job is telling the owner something is wrong.
    Silencing it because its own bookkeeping row is missing would drop the
    message in the one case it matters most. The cascade of a wrong 'allow' is
    one extra line; of a wrong 'deny', a flow that dies unannounced.
    """
    assert await may_patrol_speak(db_client, "t_missing") is True
