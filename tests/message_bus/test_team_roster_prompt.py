"""
@file_name: test_team_roster_prompt.py
@author:
@date: 2026-08-11
@description: The roster — who else is here, what they do, and are they busy.

The room used to introduce its members as a comma-separated list of display
names. Three things followed from that and all three were product problems:

  * **"who should I @" was unanswerable.** The prompt instructs the agent to
    pull a teammate in by @mentioning them, while giving it nothing to choose
    on. A team degrades into strangers who @ whoever spoke last.
  * **two surfaces, two identifier systems.** The roster hands out display
    names; `message_agent` wants an `agent_id`. The agent had to guess the
    mapping between them.
  * **nobody could tell whether a teammate was mid-task.** The activity mirror
    has fed the UI's roster for months; no agent could read it, so "don't
    interrupt someone who is working" was not a decision anyone could make.

Pinned here:
  * a row per member, in the same shape as Known Agents (`id` — name: desc)
  * the agent itself appears, marked
  * the lead is marked, and non-leads can see who it is
  * a live teammate shows a duration; an idle one shows nothing at all
  * `phase` never reaches the prompt
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.utils.timezone import utc_now


def _roster(**overrides):
    """Two members: the lead (us, by default) and a worker."""
    base = [
        {
            "agent_id": "agent_lead", "name": "Ana",
            "description": "Schedules the work and signs it off",
            "capabilities": ["lark", "web_search"],
        },
        {
            "agent_id": "agent_worker", "name": "Bruno",
            "description": "OCR and table extraction",
            "capabilities": ["ocr"],
        },
    ]
    for row in base:
        row.update(overrides.get(row["agent_id"], {}))
    return base


def _with_activity(row: dict | None, agent_id: str = "agent_worker"):
    """Roster carrying an activity row, the way `_team_roster` builds it."""
    return [
        {**r, "activity": row} if r["agent_id"] == agent_id else r
        for r in _roster()
    ]


def _prompt(agent_id: str = "agent_lead", *, roster=None, **kw) -> str:
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_u", content="status?"
    )
    return trigger._build_team_prompt(
        agent_id,
        [msg],
        roster if roster is not None else _roster(),
        owner_user_id="usr_u",
        team_id="t1",
        trigger_messages=[msg],
        lead_agent_id="agent_lead",
        work_items=[],
        bulletin=None,
        **kw,
    )


def test_a_member_row_carries_the_id_the_tools_want():
    """The roster and `message_agent` must speak the same identifier."""
    text = _prompt()

    assert "`agent_worker`" in text
    assert "Bruno" in text


def test_a_member_row_carries_what_they_are_for():
    """The basis for "who should I hand this to"."""
    text = _prompt()

    assert "OCR and table extraction" in text


def test_capabilities_reach_the_room():
    """Recomputed mechanically for every agent, and never once shown to one."""
    text = _prompt()

    assert "ocr" in text


def test_the_agent_sees_itself_in_the_roster():
    """"Where am I and who am I here" is the card's first job; leaving yourself
    off the list is the problem, not a tidy-up."""
    text = _prompt("agent_worker")

    assert "(you)" in text
    line = next(ln for ln in text.splitlines() if "(you)" in ln)
    assert "agent_worker" in line


def test_a_non_lead_can_see_who_the_lead_is():
    """The lead has been told it is the lead for a while. Nobody else was."""
    text = _prompt("agent_worker")

    lead_line = next(ln for ln in text.splitlines() if "agent_lead" in ln)
    assert "Leader" in lead_line


def test_a_busy_teammate_shows_how_long_it_has_been_busy():
    """"running" alone cannot separate "just started" from "stuck for an hour",
    and the second is the one worth reporting."""
    text = _prompt(roster=_with_activity({
        "state": "running", "started_at": utc_now() - timedelta(minutes=3),
        "updated_at": utc_now(), "phase": "tool:Read",
    }))

    worker_line = next(ln for ln in text.splitlines() if "agent_worker" in ln)
    assert "running" in worker_line
    assert "3m" in worker_line


def test_the_internal_phase_never_reaches_the_prompt():
    """`phase` is an implementation step name. Handing it to a model invites it
    to reason about a teammate's tool use, which is not its business and churns
    every few seconds."""
    text = _prompt(roster=_with_activity({
        "state": "running", "started_at": utc_now(),
        "updated_at": utc_now(), "phase": "tool:Read",
    }))

    assert "tool:Read" not in text


def test_a_dead_heartbeat_reads_as_no_signal_not_as_running():
    """The distinction the Leader acts on."""
    text = _prompt(roster=_with_activity({
        "state": "running",
        "started_at": utc_now() - timedelta(hours=1),
        "updated_at": utc_now() - timedelta(hours=1),
    }))

    worker_line = next(ln for ln in text.splitlines() if "agent_worker" in ln)
    assert "no signal" in worker_line


def test_an_idle_teammate_carries_no_status_at_all():
    """Idle is the resting state. Printing it next to every name is standing
    noise of exactly the kind this room keeps removing."""
    text = _prompt(roster=_with_activity({"state": "idle", "updated_at": utc_now()}))

    worker_line = next(ln for ln in text.splitlines() if "agent_worker" in ln)
    assert "idle" not in worker_line.lower()
    assert "running" not in worker_line


def test_an_unset_description_prints_nothing_rather_than_a_placeholder():
    """Same rule as Known Agents: printing "a new agent ready for
    configuration" beside every peer tells the model none of them are usable."""
    roster = _roster(agent_worker={"description": "a new agent ready for configuration"})
    text = _prompt(roster=roster)

    assert "ready for configuration" not in text


def test_the_ghost_member_warning_survives():
    """Paired with the "only @ people on this list" rule further down."""
    text = _prompt()

    assert "LEFT or was never" in text


@pytest.mark.asyncio
async def test_the_roster_is_fetched_in_batches_not_per_member(db_client):
    """A per-member loop is what this replaced, and it is easy to reintroduce.

    The old shape fetched each member's whole `agents` row one at a time and
    kept only the name. Nothing about the current code stops someone adding
    "just one more lookup" inside the loop, and with a few dozen members the
    cost is invisible in tests and real in a poll cycle.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    calls: list[str] = []
    bus = LocalMessageBus(backend=db_client._backend)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_1", "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    for i in range(8):
        aid = f"agent_{i}"
        await db_client.insert("bus_channel_members",
                               {"channel_id": "ch_1", "agent_id": aid})
        await db_client.insert("agents", {"agent_id": aid, "agent_name": f"A{i}",
                                          "created_by": "usr_1"})

    real_get_one = bus._db.get_one

    async def _counted(table, filters):
        calls.append(table)
        return await real_get_one(table, filters)

    bus._db.get_one = _counted  # type: ignore[method-assign]
    trigger = MessageBusTrigger(bus=bus)

    roster = await trigger._team_roster("ch_1")

    assert len(roster) == 8
    # Not one per member. `get_by_ids` is the dialect-safe batch shape the
    # repositories already use.
    assert calls.count("agents") == 0


@pytest.mark.asyncio
async def test_a_real_activity_row_reaches_the_prompt(db_client):
    """End to end, because the unit tests above hand-build the roster row.

    Nothing covered `_team_roster` → `roster[i]["activity"]` → `_member_status`
    as one chain, so a break anywhere along it — the wrong table, the wrong key,
    a per-agent read picking another room — would have left every status test
    green while no teammate status reached a real prompt.
    """
    from datetime import timedelta

    from narranexus.platform.message_bus.local_bus import LocalMessageBus
    from narranexus.platform.message_bus.schemas import BusMessage

    bus = LocalMessageBus(backend=db_client._backend)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_1", "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    for aid, name in (("agent_lead", "Ana"), ("agent_worker", "Bruno")):
        await db_client.insert("bus_channel_members",
                               {"channel_id": "ch_1", "agent_id": aid})
        await db_client.insert("agents", {"agent_id": aid, "agent_name": name,
                                          "created_by": "usr_1"})
    await db_client.insert("bus_agent_activity", {
        "agent_id": "agent_worker", "channel_id": "ch_1", "state": "running",
        "started_at": utc_now() - timedelta(minutes=4), "updated_at": utc_now(),
    })
    # A row for the SAME agent in another room, older-sorting channel id. If the
    # lookup ever goes per-agent instead of per-channel this is what it finds.
    await db_client.insert("bus_channels", {
        "channel_id": "ch_0", "name": "other", "channel_type": "group",
        "created_by": "team_t2",
    })
    await db_client.insert("bus_agent_activity", {
        "agent_id": "agent_worker", "channel_id": "ch_0", "state": "idle",
        "updated_at": utc_now(),
    })

    trigger = MessageBusTrigger(bus=bus)
    roster = await trigger._team_roster("ch_1")
    text = trigger._build_team_prompt(
        "agent_lead",
        [BusMessage(message_id="m1", channel_id="ch_1", from_agent="usr_u",
                    content="status?")],
        roster,
        owner_user_id="usr_1", team_id="t1", trigger_messages=[],
        lead_agent_id="agent_lead", work_items=[], bulletin=None,
    )

    worker_line = next(ln for ln in text.splitlines() if "agent_worker" in ln)
    assert "running (4m)" in worker_line
