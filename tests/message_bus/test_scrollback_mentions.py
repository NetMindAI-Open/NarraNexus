"""
@file_name: test_scrollback_mentions.py
@author:
@date: 2026-08-11
@description: Which lines in the room were aimed at whom.

The scrollback rendered every message as `Name: text` and dropped the one field
that says who it was for. `BusMessage.mentions` has always been there and the
team prompt never read it, so a room where three people are being coordinated
arrives as undifferentiated chatter and the agent has to guess which lines
concern it.

Worse at the pointer: when several messages @mentioned this agent in one batch,
the prompt named only `trigger_messages[-1]`. The earlier ones sat in the
scrollback looking like everyone else's traffic — asked and silently dropped,
which reads to the user as the agent ignoring them.

Pinned here:
  * a line that @mentioned someone says so
  * a line aimed at THIS agent is marked as such, not just attributed
  * every message in the batch that called this agent is pointed at, in order
  * an unaddressed line stays clean — no empty annotation
"""
from __future__ import annotations

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


ROSTER = [
    {"agent_id": "agent_me", "name": "Mia"},
    {"agent_id": "agent_peer", "name": "Pat"},
]


def _msg(content, *, sender="usr_u", mentions=None, mid="m"):
    return BusMessage(
        message_id=mid, channel_id="ch_1", from_agent=sender,
        content=content, mentions=mentions,
    )


def _prompt(history, trigger_messages):
    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    return trigger._build_team_prompt(
        "agent_me", history, ROSTER,
        owner_user_id="usr_u", team_id="t1",
        trigger_messages=trigger_messages,
        lead_agent_id="agent_peer", work_items=[], bulletin=None,
    )


def test_a_line_addressed_to_me_is_marked_as_mine():
    msg = _msg("can you take the OCR?", mentions=["agent_me"])
    text = _prompt([msg], [msg])

    line = next(ln for ln in text.splitlines() if "take the OCR" in ln)
    assert "→ you" in line


def test_a_line_addressed_to_someone_else_names_them():
    """Knowing a request already has an owner is what stops two agents doing
    the same job."""
    msg = _msg("Pat, can you draft it?", mentions=["agent_peer"])
    text = _prompt([msg], [])

    line = next(ln for ln in text.splitlines() if "draft it" in ln)
    assert "Pat" in line.split(":")[0] or "→ Pat" in line


def test_an_unaddressed_line_carries_no_annotation():
    msg = _msg("morning everyone")
    text = _prompt([msg], [])

    line = next(ln for ln in text.splitlines() if "morning everyone" in ln)
    assert "→" not in line


def test_every_message_in_the_batch_that_called_me_is_pointed_at():
    """Answering only the last one silently drops the earlier asks."""
    first = _msg("can you take the OCR?", mentions=["agent_me"], mid="m1")
    second = _msg("and the index too?", mentions=["agent_me"], mid="m2")
    text = _prompt([first, second], [first, second])

    pointer = text[text.index("@mentioned"):]
    assert "take the OCR" in pointer or "2 messages" in pointer
    assert "index too" in pointer or "2 messages" in pointer


def test_a_single_mention_still_reads_naturally():
    msg = _msg("ping", sender="agent_peer", mentions=["agent_me"])
    text = _prompt([msg], [msg])

    assert '@mentioned by "Pat"' in text
