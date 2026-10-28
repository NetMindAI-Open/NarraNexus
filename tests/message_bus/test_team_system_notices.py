"""
@file_name: test_team_system_notices.test.py
@author: NarraNexus
@date: 2026-08-12
@description: Things the room did that the room never mentioned.

Two events changed what the team could do and left no trace anywhere the user
looks:

**The cascade cap.** When agent hops pile up since the last human message, the
platform stops propagating @mentions so two agents cannot loop forever. That is
the right behaviour — and it was recorded only in a server log. From the room it
looks like the agent asked a teammate to help and the teammate ignored it. The
user asked for someone to be pulled in; the platform declined; nobody said so.

**Turn failure is a different case, and worth stating precisely**: it was
already visible, because the trigger returns "I couldn't process your message"
as the reply text. But it is posted AS THE AGENT, with the agent's name and
colour, as if the agent had said it. It is not attribution that is missing — it
is correct attribution.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES

CHANNEL = "ch_1"
TEAM = "team_1"


@pytest.fixture
async def bus(db_client):
    await db_client.insert("bus_channels", {
        "channel_id": CHANNEL, "channel_type": "group",
        "created_by": f"team_{TEAM}", "name": "T",
    })
    await db_client.insert("teams", {"team_id": TEAM, "owner_user_id": "u1", "name": "T"})
    return LocalMessageBus(backend=db_client._backend)


async def _notices(db, msg_type):
    rows = await db.execute(
        "SELECT * FROM bus_messages WHERE msg_type = %s", (msg_type,), fetch=True
    )
    return rows or []


@pytest.mark.asyncio
async def test_a_capped_cascade_says_so_in_the_room(bus, db_client):
    """The user asked for a teammate to be pulled in and the platform declined.
    A log line is not an answer to the person who asked."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL, dropped=["agent_b"], depth=4
    )

    notices = await _notices(db_client, "system_cascade")
    assert len(notices) == 1


@pytest.mark.asyncio
async def test_the_notice_names_who_was_not_reached(bus, db_client):
    """"A limit was hit" is not actionable. "Bruno was not pulled in" tells the
    user exactly who to @ themselves."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL, dropped=["agent_b"], depth=4
    )

    body = (await _notices(db_client, "system_cascade"))[0]["content"]
    assert "agent_b" in body


@pytest.mark.asyncio
async def test_the_cascade_notice_wakes_nobody(bus, db_client):
    """It is posted precisely BECAUSE the chain must stop. Carrying mentions
    would restart the loop it exists to break."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL, dropped=["agent_b"], depth=4
    )

    assert not (await _notices(db_client, "system_cascade"))[0].get("mentions")


@pytest.mark.asyncio
async def test_it_is_a_platform_line_not_a_member_speaking(bus, db_client):
    """So it renders as a centred notice, does not take an identity colour, does
    not count toward the summary threshold, and does not itself count as an
    agent hop — which would make the cap tighten every time it fired."""
    from narranexus.platform.message_bus.team_notices import CASCADE_MSG_TYPE

    assert CASCADE_MSG_TYPE in PLATFORM_MSG_TYPES


@pytest.mark.asyncio
async def test_no_notice_when_nothing_was_dropped(bus, db_client):
    """A cap that did not fire has nothing to report; announcing it would train
    the reader to ignore the line."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL, dropped=[], depth=1
    )

    assert await _notices(db_client, "system_cascade") == []


@pytest.mark.asyncio
async def test_a_missing_room_does_not_break_the_turn(bus, db_client):
    """Announcing is best-effort: the turn that hit the cap has already produced
    a real reply, and failing to narrate it must not lose that."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id="team_missing", channel_id="ch_missing",
        dropped=["agent_b"], depth=4,
    )  # must not raise


# ── membership and lead ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_joining_is_announced(bus, db_client):
    """Who is in the room changes what @all means and who can answer. Today it
    changes silently, and the transcript above the change still reads as if the
    old roster wrote it."""
    from narranexus.platform.message_bus.team_notices import post_roster_change

    await post_roster_change(
        db_client, team_id=TEAM, channel_id=CHANNEL, action="joined", agent_name="Bruno"
    )

    notices = await _notices(db_client, "system_roster")
    assert len(notices) == 1
    assert "Bruno" in notices[0]["content"]


@pytest.mark.asyncio
async def test_a_lead_change_is_announced(bus, db_client):
    """The lead is who answers when nobody is named, and who patrol wakes.
    Changing it changes who is responsible."""
    from narranexus.platform.message_bus.team_notices import post_roster_change

    await post_roster_change(
        db_client, team_id=TEAM, channel_id=CHANNEL, action="lead", agent_name="Ana"
    )

    assert len(await _notices(db_client, "system_roster")) == 1


@pytest.mark.asyncio
async def test_roster_notices_wake_nobody_either(bus, db_client):
    """Adding a member should not immediately hand them a turn."""
    from narranexus.platform.message_bus.team_notices import post_roster_change

    await post_roster_change(
        db_client, team_id=TEAM, channel_id=CHANNEL, action="joined", agent_name="Bruno"
    )

    assert not (await _notices(db_client, "system_roster"))[0].get("mentions")


# ── the wiring ──────────────────────────────────────────────────────────────
#
# Every test above calls the notice directly. The seam is where this feature
# would silently do nothing, and on this branch that has already happened once:
# a `getattr` read a name that did not exist in scope and swallowed the failure
# into a permanent None, feature dead with every test green.
#
# Three source-text assertions used to live here — that the cap site mentions
# `post_cascade_capped`, that it comes after the send, that `@everyone` is
# filtered out. They were the honest choice while nothing behavioural could
# reach this path (the only test that drove it returned a tuple where production
# reads `turn.text`, so everything past that line raised and was swallowed).
# With that fixed they are replaced by `test_team_cascade_notice_e2e.py`, which
# runs the real handler — and the third of them was pinning the bug: filtering
# `@everyone` out is exactly what made an `@all` hit the cap in silence.


@pytest.mark.asyncio
async def test_a_capped_at_all_is_announced_too(bus, db_client):
    """The most common way to hit the cap, and it used to be the silent one.

    `_extract_team_mentions` returns EITHER ``["@everyone"]`` OR a list of agent
    ids, never a mix. So when an agent says `@all` and the hop cap fires, the
    list of named teammates is empty — and the notice returned early, saying
    nothing. That is precisely the scenario this notice exists for ("the user
    asked for people to be pulled in, the platform declined, nobody said so"),
    reached through the phrasing a six-member room uses most.
    """
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL,
        dropped=[], dropped_everyone=True, depth=4,
    )

    notices = await _notices(db_client, "system_cascade")
    assert len(notices) == 1
    assert "4" in notices[0]["content"]


@pytest.mark.asyncio
async def test_a_capped_at_all_is_not_reported_as_a_person(bus, db_client):
    """"@everyone was not pulled in" is a sentence about someone who does not
    exist. The rest of the TEAM was not pulled in — that is who to name."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL,
        dropped=[], dropped_everyone=True, depth=4,
    )

    content = (await _notices(db_client, "system_cascade"))[0]["content"]
    assert "@everyone" not in content
    assert "@all" not in content


@pytest.mark.asyncio
async def test_nothing_dropped_still_says_nothing(bus, db_client):
    """Announcing a cap that did not fire trains the reader to ignore the line."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL,
        dropped=[], dropped_everyone=False, depth=4,
    )

    assert await _notices(db_client, "system_cascade") == []


@pytest.mark.asyncio
async def test_several_dropped_teammates_read_as_a_sentence(bus, db_client):
    """"Ana, Bruno was not pulled in" is not English. The line exists to be read
    by a person who then has to act on it."""
    from narranexus.platform.message_bus.team_notices import post_cascade_capped

    await post_cascade_capped(
        db_client, team_id=TEAM, channel_id=CHANNEL,
        dropped=["Ana", "Bruno"], depth=4,
    )

    content = (await _notices(db_client, "system_cascade"))[0]["content"]
    assert "Ana, Bruno were not pulled in" in content


