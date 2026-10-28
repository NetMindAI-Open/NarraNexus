"""
@file_name: test_default_responder_honesty.py
@author:
@date: 2026-08-11
@description: Telling an agent WHY it was woken, when nobody named it.

A team room is purely @mention-driven, so a user message that names nobody
would wake nobody. The route therefore picks the team's default responder —
correct, and the room would go silent without it. What it also did was write
that choice into the message's `mentions`, which made it indistinguishable from
the user actually typing that agent's name.

The turn prompt then told the agent "You were just @mentioned by User". It was
not. The difference matters to what the agent does next: being singled out by a
person is a reason to commit; being the room's fallback is a reason to check
whether someone else is the right owner and hand it over.

The fact is recorded where the choice is made — the trigger has no way to infer
it, and guessing ("one mention, and it happens to be the lead") cannot be told
apart from a user deliberately naming the lead, which is a common thing to do.

Pinned here:
  * a real @mention still reads as one
  * a routed message says so instead, and says why this agent got it
  * the routing itself is unchanged — exactly one agent still wakes
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


ROSTER = [
    {"agent_id": "agent_lead", "name": "Ana"},
    {"agent_id": "agent_worker", "name": "Bruno"},
]


def _prompt(trigger_messages):
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    return trigger._build_team_prompt(
        "agent_lead", trigger_messages, ROSTER,
        owner_user_id="usr_u", team_id="t1",
        trigger_messages=trigger_messages,
        lead_agent_id="agent_lead", work_items=[], bulletin=None,
    )


def _msg(*, routed_by=None):
    return BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_u",
        content="how is the OCR going?", mentions=["agent_lead"],
        routed_by=routed_by,
    )


def test_a_real_mention_still_reads_as_one():
    text = _prompt([_msg()])

    assert "@mentioned by User" in text


def test_a_routed_message_does_not_claim_a_mention():
    """The lie this removes. An agent that believes a person singled it out
    may over-commit on the strength of attention it never received."""
    text = _prompt([_msg(routed_by="default_responder")])

    assert "@mentioned" not in text


def test_a_routed_message_explains_why_this_agent_got_it():
    """"Why me" is answerable here and was never answered."""
    text = _prompt([_msg(routed_by="default_responder")])

    assert "default responder" in text.lower()


@pytest.mark.asyncio
async def test_the_route_records_the_choice_without_changing_it(db_client):
    """The stamp is bookkeeping. Delivery must behave exactly as before —
    getting this wrong means a team room that stops answering."""
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    bus = LocalMessageBus(backend=db_client._backend)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_1", "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    mid = await bus.send_message(
        from_agent="usr_u", to_channel="ch_1", content="anyone?",
        mentions=["agent_lead"], routed_by="default_responder",
    )

    row = await db_client.get_one("bus_messages", {"message_id": mid})
    assert row["routed_by"] == "default_responder"
    # The mention is still there: it is what wakes the agent.
    msgs = await bus.get_recent_messages("ch_1", limit=5)
    assert msgs[-1].mentions == ["agent_lead"]
    assert msgs[-1].routed_by == "default_responder"


@pytest.mark.asyncio
async def test_an_ordinary_message_carries_no_stamp(db_client):
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    bus = LocalMessageBus(backend=db_client._backend)
    await db_client.insert("bus_channels", {
        "channel_id": "ch_1", "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    await bus.send_message(
        from_agent="usr_u", to_channel="ch_1", content="hi", mentions=["agent_lead"],
    )

    assert (await bus.get_recent_messages("ch_1", limit=1))[0].routed_by is None


def test_a_routed_BATCH_does_not_claim_mentions_either():
    """The same lie, one branch over.

    Two messages arriving inside one poll window (3-12s) is ordinary, and if
    the user @mentioned nobody in either, BOTH get a synthesised mention. The
    plural branch then announced "2 messages @mentioned you" — the exact
    sentence this change exists to delete, surviving because the honesty check
    was written as "is there exactly one".
    """
    msgs = [
        BusMessage(message_id="m1", channel_id="ch_1", from_agent="usr_u",
                   content="how is the OCR going?", mentions=["agent_lead"],
                   routed_by="default_responder"),
        BusMessage(message_id="m2", channel_id="ch_1", from_agent="usr_u",
                   content="and the index?", mentions=["agent_lead"],
                   routed_by="default_responder"),
    ]

    text = _prompt(msgs)

    assert "@mentioned you" not in text
    assert "default responder" in text.lower()
    # Still both, still in order — the multi-message fix must survive.
    assert text.index("how is the OCR") < text.index("and the index")


def test_a_mixed_batch_marks_which_ones_were_routed():
    """One real @ and one fallback in the same batch. Calling the whole batch
    either name would be false about half of it, so each line says which it is
    — the scrollback already proved per-line annotation reads fine."""
    msgs = [
        BusMessage(message_id="m1", channel_id="ch_1", from_agent="usr_u",
                   content="nobody named", mentions=["agent_lead"],
                   routed_by="default_responder"),
        BusMessage(message_id="m2", channel_id="ch_1", from_agent="usr_u",
                   content="Ana, look at this", mentions=["agent_lead"]),
    ]

    text = _prompt(msgs)

    # The pointer section, not the scrollback — both texts appear in each.
    pointer = text[text.index("Reply to the ones that ask you"):]
    routed_line = next(ln for ln in pointer.splitlines() if "nobody named" in ln)
    real_line = next(ln for ln in pointer.splitlines() if "Ana, look at this" in ln)
    assert "no @mention" in routed_line
    assert "no @mention" not in real_line
