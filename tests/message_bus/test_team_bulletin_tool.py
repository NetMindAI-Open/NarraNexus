"""
@file_name: test_team_bulletin_tool.py
@author: NarraNexus
@date: 2026-08-10
@description: Agents can pin a rule the team agreed on — and only that.

方案 B lets the team sediment its own conventions ("we settled on report
format v2") instead of the user restating them. That is only safe because the
authority is asymmetric, and the asymmetry is what these tests pin:

  * an agent may add, and may retract WHAT IT ITSELF WROTE;
  * an agent may never touch a user's entry. Otherwise a model that finds a
    rule inconvenient can delete the rule constraining it — and the bulletin
    is exactly where the constraining rules live;
  * an agent may never touch the auto-summary, which no one authored and
    which it would otherwise be able to rewrite into an instruction.

The team a write lands in comes from the SERVER-SIDE identity of the turn, not
from a tool argument. `team_share_file` takes team_id from the model and
checks it three ways; here the stronger option exists because the turn already
carries its team, so naming another team is not a permission failure to catch
— it is not expressible. A private turn therefore has no bulletin to write to
at all.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.team_bulletin import (
    post_team_bulletin,
    remove_team_bulletin,
)
from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)

OWNER = "user_1"
TEAM = "team_1"
AGENT = "agent_a"


async def _seed(db, *, agent=AGENT, member=True):
    await db.insert("agents", {"agent_id": agent, "agent_name": agent, "created_by": OWNER})
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    if member:
        await db.insert("team_members", {"team_id": TEAM, "agent_id": agent})


# ── writing ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_can_pin_a_rule(db_client):
    await _seed(db_client)
    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="report format v2")
    assert res["success"] is True

    entries = await TeamBulletinRepository(db_client).list_for_team(TEAM)
    assert [e.content for e in entries] == ["report format v2"]


@pytest.mark.asyncio
async def test_an_agent_entry_records_who_wrote_it(db_client):
    """Attribution is what makes agent write access reviewable: the user needs
    to see which rules the team invented for itself."""
    await _seed(db_client)
    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="ours")
    entry = (await TeamBulletinRepository(db_client).list_for_team(TEAM))[0]
    assert entry.source == "agent"
    assert entry.author_id == AGENT


@pytest.mark.asyncio
async def test_a_turn_with_no_team_cannot_write_at_all(db_client):
    """A one-to-one turn carries no team identity, so there is no bulletin to
    address. Not an authorisation failure — an unaddressable one."""
    await _seed(db_client)
    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id="", content="sneaky")
    assert res["success"] is False
    assert "team" in res["error"].lower()


@pytest.mark.asyncio
async def test_a_non_member_cannot_write(db_client):
    """One owner has many teams; being the owner's agent is not membership."""
    await _seed(db_client, member=False)
    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="not mine to set")
    assert res["success"] is False
    assert await TeamBulletinRepository(db_client).list_for_team(TEAM) == []


@pytest.mark.asyncio
async def test_an_over_budget_write_is_refused_with_the_reason(db_client):
    """The agent shares the user's budget and gets the same explanation, so it
    can shorten and retry rather than loop on an opaque failure."""
    await _seed(db_client)
    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="x" * 5000)
    assert res["success"] is False
    assert "500" in res["error"]


# ── the asymmetry ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_agent_can_retract_its_own_entry(db_client):
    await _seed(db_client)
    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="mine")
    repo = TeamBulletinRepository(db_client)
    entry = (await repo.list_for_team(TEAM))[0]

    res = await remove_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=entry.entry_id)
    assert res["success"] is True
    assert await repo.list_for_team(TEAM) == []


@pytest.mark.asyncio
async def test_an_agent_cannot_delete_a_user_entry(db_client):
    """The one that matters. The bulletin is where the rules constraining the
    agent live, so an agent able to delete them can delete its own leash."""
    await _seed(db_client)
    repo = TeamBulletinRepository(db_client)
    users = await repo.add(team_id=TEAM, content="always use Chinese", source="user", author_id=OWNER)

    res = await remove_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=users.entry_id)

    assert res["success"] is False
    assert await repo.get(users.entry_id) is not None


@pytest.mark.asyncio
async def test_an_agent_cannot_delete_another_agents_entry(db_client):
    """Teammates are not interchangeable: one agent retracting another's stated
    convention is the same silent overwrite, one layer along."""
    await _seed(db_client)
    repo = TeamBulletinRepository(db_client)
    theirs = await repo.add(team_id=TEAM, content="Bob's rule", source="agent", author_id="agent_b")

    res = await remove_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=theirs.entry_id)

    assert res["success"] is False
    assert await repo.get(theirs.entry_id) is not None


@pytest.mark.asyncio
async def test_an_agent_cannot_delete_the_auto_summary(db_client):
    """Nobody authored it, so nobody may retract it as their own."""
    await _seed(db_client)
    repo = TeamBulletinRepository(db_client)
    await repo.upsert_summary(TEAM, "progress")
    summary = await repo.get_summary(TEAM)

    res = await remove_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=summary.entry_id)

    assert res["success"] is False
    assert await repo.get_summary(TEAM) is not None


@pytest.mark.asyncio
async def test_an_agent_cannot_reach_another_teams_entry(db_client):
    """The turn's team bounds the write; an entry_id from elsewhere is not a
    key that opens this door."""
    await _seed(db_client)
    repo = TeamBulletinRepository(db_client)
    elsewhere = await repo.add(team_id="team_2", content="theirs", source="agent", author_id=AGENT)

    res = await remove_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=elsewhere.entry_id)

    assert res["success"] is False
    assert await repo.get(elsewhere.entry_id) is not None


# ── registration and identity ───────────────────────────────────────────────


def test_the_tools_take_no_team_argument():
    """The turn's team comes from the server-side identity headers. A team_id
    parameter would hand the model the ability to name a team it is not working
    in — the thing this design removes rather than validates."""
    import inspect

    from narranexus_plugins.message_bus_module import _message_bus_mcp_tools as m

    src = inspect.getsource(m)
    pin = src[src.index("async def team_pin_rule(") :]
    signature = pin[: pin.index(")")]
    assert "team_id" not in signature
    assert "caller_team_id_from_request()" in pin[: pin.index("async def team_unpin_rule")]


def test_the_agent_is_told_the_tool_exists():
    """A tool the prompt never mentions is a tool that never gets used."""
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger

    prompt = MessageBusTrigger(bus=None)._build_team_prompt(
        "agent_b",
        [],
        [
            {"agent_id": "agent_a", "name": "Alice"},
            {"agent_id": "agent_b", "name": "Bob"},
        ],
        owner_user_id="user_a",
        team_id="team_42",
        bulletin=None,
    )
    assert "team_pin_rule" in prompt


def test_the_prompt_discourages_using_the_bulletin_as_a_notepad():
    """The budget is small and shared with the user's rules; an agent that
    pins findings crowds out the rules it is meant to obey."""
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger

    prompt = MessageBusTrigger(bus=None)._build_team_prompt(
        "agent_b",
        [],
        [
            {"agent_id": "agent_a", "name": "Alice"},
            {"agent_id": "agent_b", "name": "Bob"},
        ],
        owner_user_id="user_a",
        team_id="team_42",
        bulletin=None,
    )
    assert "chat, not the bulletin" in prompt


# ── an agent's write announces itself too ───────────────────────────────────
#
# The notice helper was first wired only to the REST routes, so a rule an AGENT
# pinned changed how the whole team behaved and left no mark in the room. The
# acceptance criterion says a system message appears when the bulletin updates;
# half the writers were skipping it.


async def _seed_room(db):
    await db.insert("bus_channels", {
        "channel_id": "ch1", "channel_type": "group",
        "created_by": f"team_{TEAM}", "name": "T",
    })


async def _notices(db):
    rows = await db.execute(
        "SELECT * FROM bus_messages WHERE msg_type = %s", ("system_bulletin",), fetch=True
    )
    return rows or []


@pytest.mark.asyncio
async def test_an_agent_pinning_a_rule_leaves_a_notice(db_client):
    await _seed(db_client)
    await _seed_room(db_client)

    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="v2")

    assert len(await _notices(db_client)) == 1


@pytest.mark.asyncio
async def test_the_notice_names_the_agent_that_wrote_it(db_client):
    """The transcript resolves from_agent to a display name, which is how the
    line localises — and how a reader tells a teammate's rule from the owner's."""
    await _seed(db_client)
    await _seed_room(db_client)

    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="v2")

    assert (await _notices(db_client))[0]["from_agent"] == AGENT


@pytest.mark.asyncio
async def test_the_notice_wakes_nobody(db_client):
    """A mention would wake every member the moment a rule is written, and a
    rule written BY an agent would wake the agents that might write another."""
    await _seed(db_client)
    await _seed_room(db_client)

    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="v2")

    assert not (await _notices(db_client))[0].get("mentions")


@pytest.mark.asyncio
async def test_retracting_a_rule_also_leaves_a_notice(db_client):
    await _seed(db_client)
    await _seed_room(db_client)
    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="v2")

    await remove_team_bulletin(
        db=db_client, agent_id=AGENT, team_id=TEAM, entry_id=res["entry_id"]
    )

    assert len(await _notices(db_client)) == 2


@pytest.mark.asyncio
async def test_a_refused_write_leaves_no_notice(db_client):
    """Announcing a change that did not happen is worse than silence."""
    await _seed(db_client, member=False)
    await _seed_room(db_client)

    await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="nope")

    assert await _notices(db_client) == []


@pytest.mark.asyncio
async def test_a_team_with_no_room_does_not_break_the_write(db_client):
    """A bulletin edit must not conjure a chat channel, and must not fail
    because there is nobody to tell."""
    await _seed(db_client)

    res = await post_team_bulletin(db=db_client, agent_id=AGENT, team_id=TEAM, content="v2")

    assert res["success"] is True
