"""
@file_name: test_lead_role_prompt.py
@author:
@date: 2026-08-07
@description: The lead's role, stated to the lead.

`teams.lead_agent_id` had exactly two consumers before this: the default
responder for un-@mentioned messages, and a badge in the roster UI. On the
AGENT side it meant nothing — the lead did not know it was the lead, which is
why "the Leader should chase stalled work" had nowhere to attach.

Pinned here:
  * the lead is told it is the lead, and told what that obliges
  * a non-lead member gets none of it (its prompt must not grow a
    responsibility it does not have)
  * the board's current contents ride the same prompt, so "what is on my
    board" never depends on the model remembering to call a tool
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


# The builder takes a roster now — the same members, with the fields the
# team card renders. Assertions below are unchanged.
MEMBERS = [
    {"agent_id": "agent_lead", "name": "Ana"},
    {"agent_id": "agent_worker", "name": "Bruno"},
]


def _prompt(agent_id: str, *, lead_agent_id: str = "agent_lead", board=None) -> str:
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_u", content="status?"
    )
    return trigger._build_team_prompt(
        agent_id,
        [msg],
        MEMBERS,
        owner_user_id="usr_u",
        team_id="t1",
        trigger_messages=[msg],
        lead_agent_id=lead_agent_id,
        work_items=board or [],
        bulletin=None,
    )


def test_the_lead_is_told_it_is_the_lead():
    text = _prompt("agent_lead")

    assert "Leader" in text
    # The obligations, not just the label — a title with no duties attached is
    # what the product had before.
    assert "team_work_add" in text


def test_a_member_is_not_given_the_leader_section():
    text = _prompt("agent_worker")

    assert "you are the Leader" not in text.lower()


def test_a_team_without_a_lead_gets_no_leader_section():
    """No lead set = nobody is responsible. The platform does not appoint one."""
    text = _prompt("agent_lead", lead_agent_id="")

    assert "you are the Leader" not in text.lower()


def test_the_board_rides_the_prompt():
    """The board is injected, not fetched.

    If the lead had to call a tool to see its own board, "what did I hand out"
    would depend on the model choosing to look — the same class of dependency
    iron rule #15 keeps off correctness-critical paths.
    """
    board = [
        {"item_id": "wi_1", "title": "OCR the scans", "assignee_id": "agent_worker",
         "status": "in_progress"},
        {"item_id": "wi_2", "title": "draft summary", "assignee_id": "",
         "status": "open"},
    ]

    text = _prompt("agent_lead", board=board)

    assert "OCR the scans" in text
    assert "draft summary" in text
    # Unclaimed work must be visibly unclaimed — that is the thing a lead is
    # supposed to notice and hand out.
    assert "unclaimed" in text.lower()


def test_an_empty_board_says_so_rather_than_showing_a_blank_section():
    text = _prompt("agent_lead", board=[])

    # A silent absence reads as "the section failed to render"; an explicit
    # empty state reads as "there is nothing on the board".
    assert "no open work items" in text.lower()


def test_members_see_the_board_too():
    """A member needs it to know what it owns — it just gets no duty to chase."""
    board = [{"item_id": "wi_1", "title": "OCR the scans",
              "assignee_id": "agent_worker", "status": "in_progress"}]

    text = _prompt("agent_worker", board=board)

    assert "OCR the scans" in text
