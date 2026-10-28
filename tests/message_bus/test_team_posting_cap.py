"""
@file_name: test_team_posting_cap.py
@date: 2026-08-17
@description: The hop cap has to RUN on the path the tool actually takes.

`team_cascade_depth` was lifted out of `MessageBusTrigger`, where `self._bus._db`
is the RAW backend and SQL goes through verbatim. Every caller of the extracted
function holds an `AsyncDatabaseClient` instead, which has no `.placeholder` — so
reading one raised inside the cap check and took the whole send with it: every
`message_team` call returned `{"success": false}` while the room stayed silent.

It surfaced only as a log line during an unrelated test, which is why this file
exists: the cap is a loop-breaker, and a loop-breaker that raises is worse than
one that is absent, because the send fails too.
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.team_posting import (
    MAX_TEAM_AGENT_HOPS,
    post_team_reply,
    team_cascade_depth,
)
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)

TEAM, CHANNEL = "t_cap", "ch_cap"
A, B, USER = "agent_a", "agent_b", f"{USER_SENDER_PREFIX}u1"


async def _room(db):
    await db.insert("bus_channels", {
        "channel_id": CHANNEL, "name": "cap", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    for aid, nm in ((A, "Ana"), (B, "Bo")):
        await db.insert("bus_channel_members", {"channel_id": CHANNEL, "agent_id": aid})
        await db.insert("agents", {"agent_id": aid, "agent_name": nm, "created_by": "u1"})
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": "u1", "name": "Cap"})


@pytest.mark.asyncio
async def test_the_cap_query_runs_against_the_client(db_client):
    """The regression itself: reading `.placeholder` off the client raised."""
    await _room(db_client)
    assert await team_cascade_depth(db_client, CHANNEL) == 0


@pytest.mark.asyncio
async def test_a_reply_lands_and_resolves_its_mentions(db_client):
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    roster = [{"agent_id": A, "name": "Ana"}, {"agent_id": B, "name": "Bo"}]

    out = await post_team_reply(
        db=db_client, bus=bus, agent_id=A, team_id=TEAM,
        channel_id=CHANNEL, text="@Bo over to you", roster=roster,
    )

    assert out["message_id"]
    assert out["mentioned"] == [B]
    assert out["capped"] == {"names": [], "everyone": False}


@pytest.mark.asyncio
async def test_the_cap_fires_and_names_who_was_not_reached(db_client):
    """A human word resets the count, so the pile has to be agent-only."""
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    roster = [{"agent_id": A, "name": "Ana"}, {"agent_id": B, "name": "Bo"}]

    for i in range(MAX_TEAM_AGENT_HOPS):
        await bus.send_message(from_agent=A, to_channel=CHANNEL, content=f"hop {i}")

    out = await post_team_reply(
        db=db_client, bus=bus, agent_id=A, team_id=TEAM,
        channel_id=CHANNEL, text="@Bo still there?", roster=roster,
    )

    assert out["mentioned"] == []
    assert out["capped"]["names"] == ["Bo"]


@pytest.mark.asyncio
async def test_a_human_word_resets_the_count(db_client):
    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)
    for i in range(MAX_TEAM_AGENT_HOPS):
        await bus.send_message(from_agent=A, to_channel=CHANNEL, content=f"hop {i}")
    await bus.send_message(from_agent=USER, to_channel=CHANNEL, content="carry on")

    assert await team_cascade_depth(db_client, CHANNEL) == 0


@pytest.mark.asyncio
async def test_platform_rows_are_excluded_in_the_query_not_after_it(db_client):
    """The invariant the hop cap actually rests on, on the LIVE implementation.

    The depth query reads a fixed `LIMIT MAX_TEAM_AGENT_HOPS + 2` newest-first
    and counts agent rows until it meets a human one. Platform notices inside
    that window do two things at once: they pad the count, and they push the
    human message — the reset — out of the window entirely. So filtering them
    out AFTER the fetch cannot work; the exclusion has to be in the WHERE.

    Laid out so the two readings differ by a definite number rather than by a
    threshold. One user message, three real hops, then four platform notices:

      * exclusion in SQL   -> hop3, hop2, hop1, usr  -> depth 3 (correct)
      * filtered afterwards -> 4 notices + hop3, hop2 -> depth 6, and the user
        message is never reached, so the reset is invisible

    Six is over the cap, so the room would start stripping @mentions off a
    three-hop chain the user had just restarted. The previous version of this
    test asserted `depth >= MAX_TEAM_AGENT_HOPS`, which BOTH readings satisfy —
    it was green on the mutation, and so was the copy of it that used to live in
    `test_patrol_turn.py` against the trigger's now-deleted `_team_cascade_depth`.
    The invariant was documented, not tested.

    There is a MySQL twin in `test_team_posting_mysql.py` — the exclusion is
    written in SQL, so it is dialect-visible — and this is the lane that runs on
    every commit.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES
    from narranexus.platform.message_bus.team_posting import (
        MAX_TEAM_AGENT_HOPS,
        team_cascade_depth,
    )

    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(from_agent=USER, to_channel=CHANNEL, content="restart")
    for i in range(3):
        await bus.send_message(
            from_agent=A, to_channel=CHANNEL, content=f"hop{i}"
        )
    for i, mt in enumerate(sorted(PLATFORM_MSG_TYPES)[:4]):
        await bus.send_message(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}", to_channel=CHANNEL,
            content=f"notice{i}", msg_type=mt,
        )

    depth = await team_cascade_depth(db_client, CHANNEL)
    assert depth == 3, (
        f"expected the three real hops, got {depth} — platform rows are being "
        f"counted as agent hops and hiding the human reset behind them"
    )
    assert depth < MAX_TEAM_AGENT_HOPS, (
        "a chain the user had just restarted would have its @mentions stripped"
    )


@pytest.mark.asyncio
async def test_blank_text_is_refused_rather_than_posted(db_client):
    """An empty room post is worse than the silence it replaced.

    `message_team` validated `team_id` from the start and never validated `text`,
    which is the half that matters: blank text posts an empty bubble into a
    surface the owner reads, and `has_message_from_turn` then answers True — so
    the "said nothing" notice is suppressed and the turn files as DELIVERED. The
    pre-redesign silence at least produced a notice.

    Two layers, because they catch different callers: the tool refuses with an
    error the model can act on (a `success: true` no-op would teach it that it
    replied), and `post_team_reply` raises — production cannot reach that, since
    the tool guards first, so anything arriving there skipped the tool. The test
    helper `speak_in_room` is such a caller, which is the point: without this a
    team test could post nothing and assert a delivery.
    """
    from narranexus.platform.message_bus.team_posting import post_team_reply

    await _room(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    for blank in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            await post_team_reply(
                db=db_client, bus=bus, agent_id=A, team_id=TEAM,
                channel_id=CHANNEL, text=blank, roster=[{"agent_id": A, "name": "Ana"}],
            )

    assert await db_client.get("bus_messages", {"channel_id": CHANNEL}) == [], (
        "a blank post reached the room"
    )


# ── The hop cap is tunable: 4 was too small for real multi-agent tasks ──────
#
# 4 consecutive agent hops is not enough for a team to finish an ordinary task
# without a human having to nudge it every few turns. The cap stays a finite
# loop-breaker (runaway agent-to-agent @storms are a real incident class), but
# the number is now raised and overridable per deployment via an env var so it
# can be tuned without a code change.


def test_hop_cap_default_is_large_enough_for_real_tasks():
    # Assert the DEFAULT constant, not the env-resolved module value — the
    # latter would go red on any host that happens to set TEAM_MAX_AGENT_HOPS.
    from narranexus.platform.message_bus.team_posting import (
        _DEFAULT_MAX_TEAM_AGENT_HOPS,
    )

    assert _DEFAULT_MAX_TEAM_AGENT_HOPS == 30, (
        "the default autonomous-hop budget should be raised well above the old 4"
    )


def test_hop_cap_reads_env_override(monkeypatch):
    from narranexus.platform.message_bus.team_posting import _resolve_hop_cap

    monkeypatch.setenv("TEAM_MAX_AGENT_HOPS", "50")
    assert _resolve_hop_cap() == 50


def test_hop_cap_clamps_absurd_values(monkeypatch):
    from narranexus.platform.message_bus.team_posting import (
        _MAX_TEAM_AGENT_HOPS_CEILING,
        _resolve_hop_cap,
    )

    monkeypatch.setenv("TEAM_MAX_AGENT_HOPS", "100000")
    assert _resolve_hop_cap() == _MAX_TEAM_AGENT_HOPS_CEILING, (
        "an over-ceiling value feeds a SQL LIMIT and must be clamped"
    )


def test_hop_cap_falls_back_on_bad_env(monkeypatch):
    from narranexus.platform.message_bus.team_posting import _resolve_hop_cap

    for bad in ("", "   ", "not-a-number", "0", "-3"):
        monkeypatch.setenv("TEAM_MAX_AGENT_HOPS", bad)
        assert _resolve_hop_cap() == 30, (
            f"a non-positive / unparseable override ({bad!r}) must fall back to "
            "the default, never disable the loop-breaker"
        )

    monkeypatch.delenv("TEAM_MAX_AGENT_HOPS", raising=False)
    assert _resolve_hop_cap() == 30
