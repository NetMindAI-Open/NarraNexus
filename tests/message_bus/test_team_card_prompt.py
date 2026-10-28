"""
@file_name: test_team_card_prompt.py
@author:
@date: 2026-08-11
@description: The team card — what a member is told about the team it is in.

A member woke up knowing its own name, that it was "in a team group chat", and
a comma-separated list of teammate names. Not what the team is called, not what
it exists to do. The owner fills in a description and an intro in the management
UI believing they are setting the team's terms; neither field had a single
consumer on the agent side, so the answer to "why are we all here" was never in
the room.

Pinned here:
  * the team's name and description ride the prompt
  * `intro_md` rides it too, bounded, and says so when it was cut
  * an unset field renders as nothing, never as an empty heading
  * the card sits ahead of the how-to-work sections — "where am I and with
    whom" is not a footnote to "here is the shared folder"
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


ROSTER = [
    {"agent_id": "agent_lead", "name": "Ana"},
    {"agent_id": "agent_worker", "name": "Bruno"},
]


def _prompt(agent_id: str = "agent_lead", *, team=None, **kw) -> str:
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    msg = BusMessage(
        message_id="m1", channel_id="ch_1", from_agent="usr_u", content="status?"
    )
    return trigger._build_team_prompt(
        agent_id,
        [msg],
        ROSTER,
        owner_user_id="usr_u",
        team_id="t1",
        trigger_messages=[msg],
        lead_agent_id="agent_lead",
        work_items=[],
        bulletin=None,
        team=team,
        **kw,
    )


def test_the_team_has_a_name_and_a_reason_to_exist():
    text = _prompt(team={
        "name": "Dunhuang Desk",
        "description": "Digitise the scanned manuscripts and publish them.",
    })

    assert "Dunhuang Desk" in text
    assert "Digitise the scanned manuscripts" in text


def test_the_intro_rides_the_prompt():
    """The owner writes this expecting the team to follow it. Until now nobody
    on the agent side ever read the field."""
    text = _prompt(team={"name": "Desk", "intro_md": "## House rules\nAlways cite the folio."})

    assert "Always cite the folio." in text


def test_a_long_intro_is_cut_and_says_so():
    """`intro_md` is MEDIUMTEXT — an owner can paste a manual into it. An
    unbounded field in a per-turn prompt would crowd out the scrollback and the
    roster, which are the parts that decide what the agent DOES this turn."""
    from narranexus.platform.message_bus.message_bus_trigger import TEAM_INTRO_MAX_CHARS

    long_intro = "x" * (TEAM_INTRO_MAX_CHARS + 500)
    text = _prompt(team={"name": "Desk", "intro_md": long_intro})

    assert "truncated" in text.lower()
    # Pin the truncation directly, not via a total-length budget: the intro is
    # all "x", so the "x" it contributes must be capped at TEAM_INTRO_MAX_CHARS
    # (the rest of the fixed prompt holds only a handful of stray "x"s). This is
    # immune to the fixed prompt legitimately growing — a new standing rule must
    # not be able to fail an intro-truncation test.
    assert text.count("x") <= TEAM_INTRO_MAX_CHARS + 100


def test_an_intro_that_fits_carries_no_truncation_notice():
    """A truncation marker on complete text is a small lie that costs trust in
    every other marker."""
    text = _prompt(team={"name": "Desk", "intro_md": "Short and complete."})

    assert "Short and complete." in text
    assert "truncated" not in text.lower()


def test_missing_fields_render_as_nothing():
    """No empty headings. A blank "Why this team exists:" reads as "the team has
    no purpose", which is worse than not raising the question."""
    text = _prompt(team={"name": "Desk"})

    assert "Desk" in text
    assert "why this team exists" not in text.lower()


def test_no_team_row_still_produces_a_working_prompt():
    """The card is best-effort context; the room conversation is the turn."""
    text = _prompt(team=None)

    assert "[Team Group Chat]" in text
    assert "Speak in this room by calling message_team" in text


def test_the_card_comes_before_the_working_instructions():
    """"Where am I, with whom, and why" is the frame the rest is read through —
    it cannot sit below the shared-folder mechanics."""
    text = _prompt(team={"name": "Dunhuang Desk", "description": "Digitise."})

    assert text.index("Dunhuang Desk") < text.index("Team shared folder")
