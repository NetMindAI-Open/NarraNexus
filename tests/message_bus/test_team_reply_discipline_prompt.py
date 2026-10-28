"""
@file_name: test_team_reply_discipline_prompt.py
@author:
@date: 2026-09-03
@description: The team-room prompt asks for replies with substance, not for
replies to everything.

Owner feedback on 2026-09-03: "team collaboration is too verbose; agents
interacting with each other is exhausting". One dev room was the exhibit — a
19-character question followed by 23 agent messages, each opening with 收到,
restating a correction the previous message had accepted, and @mentioning
both other members. The prompt had been asking for exactly that: "Address ALL
of them" made an FYI owe a reply, and "hand it to whoever is the better owner
by @mentioning them" made hand-off the default move.

These pin the replacement wording. A prompt test is a weak test, and the
repository knows it (iron rule #15: the prompt is not the guard) — the guards
are `opens_handoffs` and the silent-turn marker, tested elsewhere. This one
only stops the two contradictory instructions from creeping back.
"""
from __future__ import annotations

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage

ROSTER = [
    {"agent_id": "agent_lead", "name": "Lead", "description": "", "capabilities": []},
    {"agent_id": "agent_ana", "name": "Ana", "description": "", "capabilities": []},
]


def _prompt(trigger_messages):
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    return trigger._build_team_prompt(
        "agent_lead", trigger_messages, ROSTER,
        owner_user_id="usr_u", team_id="t1",
        trigger_messages=trigger_messages,
        lead_agent_id="agent_lead", work_items=[], bulletin=None,
    )


def _msg(i, *, routed_by=None, from_agent="usr_u"):
    return BusMessage(
        message_id=f"m{i}", channel_id="ch_1", from_agent=from_agent,
        content=f"message {i}", mentions=["agent_lead"], routed_by=routed_by,
    )


def test_a_batch_asks_for_replies_to_asks_not_to_everything():
    text = _prompt([_msg(1), _msg(2)])
    assert "Address ALL of them" not in text
    assert "Reply to the ones that ask you for something" in text
    assert "needs no reply" in text


def test_a_routed_batch_says_the_same():
    text = _prompt([_msg(1, routed_by="default_responder"),
                    _msg(2, routed_by="default_responder")])
    assert "Address ALL of them" not in text
    assert "Reply to the ones that ask you for something" in text


def test_the_default_responder_is_told_to_answer_not_to_forward():
    text = _prompt([_msg(1, routed_by="default_responder")])
    assert "Answer it yourself" in text
    assert "do not also half-answer" in text
    assert "whoever on the roster is the better owner" not in text


def test_the_writing_rules_name_the_three_noise_moves():
    text = _prompt([_msg(1)])
    # acknowledgement openers, restating, thread wrap-ups
    assert "Do not open with an acknowledgement" in text
    assert "do not restate what a teammate already said" in text
    assert "wrap-up" in text
    # courtesy @mentions
    assert "Reply to whoever asked you, and only to them" in text
    # silence is explicitly cost-free now that it leaves no room line
    assert "leaves no mark in the room" in text
