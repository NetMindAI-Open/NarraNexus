"""
@file_name: test_surface_matrix.py
@author:
@date: 2026-08-18
@description: Owner / team / peer / patrol / one IM surface, both halves: what
              the desk holds and what the turn's opening line says about it.

Named rather than "every surface", because it is not every surface — `job` and five
of the six IM sources are absent. One IM row is representative on purpose: the six
channel modules all inherit `claims_source` from `ChannelModuleBase`, so
origin-first ordering resolves identically for them, whereas ChatModule and
MessageBusModule override it and are the pair the desk bug lived in.

Spec §10 item 2. Two independent mechanisms decide what an agent is told to do
this turn:

  * the DESK — `expressive_tools` names the turn's reply tool, and
    `disallowed_tools` removes the other candidates' schemas;
  * the DECLARATION — `render_origin_declaration` writes the `[Origin] … reply
    with …` line at the top of the turn.

They are built from the same tuple by design, so they cannot disagree in
principle. In practice the platform shipped with them disagreeing on EVERY team
turn, because the runtime asks for suppression before declaration and one module
was reading the turn from state the declaration had not written yet. Both hooks
were individually correct; nothing looked at a whole surface at once. That is
what this file does, and it is per-surface rather than a single case because the
bug was surface-specific — peer DMs were fine throughout.

Each row asserts three things that together mean "this turn is answerable":
  1. something is declared (a surface with no reply tool cannot answer);
  2. nothing declared is also suppressed (the reminder names a callable tool);
  3. the opening line exists and names the declared default (so prose and desk
     tell one story) — except where the surface deliberately has no reply tool,
     which must produce NO line rather than an invented one.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import narranexus.platform.message_bus  # noqa: F401 — registers the bus handler
from narranexus_plugins.frameworks_claude_code.prompts import (
    append_reply_reminder,
)
from narranexus.platform.channel.message_source_handler import (
    render_origin_declaration,
)
from narranexus.platform.schema import (
    BUS_PLAIN_TEXT_TURN_EXTRA_KEY,
    BUS_TEAM_ROOM_EXTRA_KEY,
)

from .test_expressive_collection import AGENT_ID, _desk

#: (label, working_source, extra_data, expected default tool or None)
#: `None` means the surface must declare NOTHING and get NO opening line.
SURFACES = [
    ("owner chat", "chat", None, "mcp__chat_module__reply_owner"),
    ("team room", "message_bus", {BUS_TEAM_ROOM_EXTRA_KEY: True},
     "mcp__message_bus_module__message_team"),
    ("peer DM", "message_bus", None, "mcp__message_bus_module__message_agent"),
    # One IM surface: the six channel modules share `ChannelModuleBase`'s
    # `claims_source`, so this row covers the inherited path that neither
    # ChatModule nor MessageBusModule exercises.
    ("IM (lark)", "lark", None, "mcp__lark_module__lark_cli"),
    ("patrol", "message_bus",
     {BUS_TEAM_ROOM_EXTRA_KEY: True, BUS_PLAIN_TEXT_TURN_EXTRA_KEY: True}, None),
]


def _instances():
    """Fresh module instances per row.

    Deliberately fresh: the defect this file exists for was a module answering
    about the PREVIOUS turn, which a loop reusing instances would hide.
    """
    from narranexus_plugins.chat_module.chat_module import ChatModule
    from narranexus_plugins.lark_module.lark_module import LarkModule
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    chat = ChatModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    bus = MessageBusModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    lark = LarkModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    # A bound channel: an unbound one suppresses every non-setup tool
    # (`ChannelModuleBase.disallowed_tools`), which is a different property
    # with its own test in `tests/channel/test_setup_residency.py`.
    lark._bound_cache = True  # type: ignore[attr-defined]
    return [
        SimpleNamespace(module_class="ChatModule", module=chat, instance_id="i1"),
        SimpleNamespace(module_class="MessageBusModule", module=bus, instance_id="i2"),
        SimpleNamespace(module_class="LarkModule", module=lark, instance_id="i3"),
    ]


@pytest.mark.parametrize("label,source,extra,expected_default", SURFACES)
@pytest.mark.asyncio
async def test_the_desk_and_the_opening_line_agree_on_this_surface(
    monkeypatch, label, source, extra, expected_default
):
    declared, suppressed = await _desk(
        _instances(), monkeypatch, working_source=source, extra=extra
    )
    # The same three inputs `step_3` composes it from.
    line = render_origin_declaration(
        source, declared,
        reply_is_plain_text=bool((extra or {}).get(BUS_PLAIN_TEXT_TURN_EXTRA_KEY)),
    )

    if expected_default is None:
        # A surface whose reply IS its plain text. Two separate claims:
        #
        # * no SEND verb for this source may be declared or on the desk — the
        #   patrol prompt forbids `message_team` in so many words, and a schema
        #   left on the desk is how a prose prohibition loses;
        # * NOTHING declared at all. A reply reminder is rendered from the
        #   declared set, so `notify_owner` in it reads "reply with `notify_owner`"
        #   on a turn whose prompt says "write plain text, do NOT call a tool" —
        #   pushing the lead to DM the owner instead of writing the room's status
        #   line (I1). ChatModule withdraws that DECLARATION on a plain-text turn;
        #   notify_owner's SCHEMA still stays (escalating mid-sweep is legitimate,
        #   asserted below).
        # * no opening line at all.
        bus_verbs = {
            "mcp__message_bus_module__message_team",
            "mcp__message_bus_module__message_agent",
        }
        assert not (set(declared) & bus_verbs), (
            f"{label}: declared a send verb on a plain-text turn: {declared}"
        )
        assert bus_verbs <= set(suppressed), (
            f"{label}: a send verb's schema is still on the desk: {suppressed}"
        )
        assert declared == [], (
            f"{label}: declared {declared} on a plain-text turn — the reply "
            f"reminder would then name a tool the patrol prompt forbids"
        )
        # The reminder is rendered from `declared`; empty declared → no reminder.
        assert append_reply_reminder("body", tuple(declared), line) == "body", (
            f"{label}: a reply reminder rendered on a plain-text turn"
        )
        # But the escalation capability stays: notify_owner's schema is NOT
        # suppressed — only its declaration is withheld.
        assert "mcp__chat_module__notify_owner" not in suppressed, (
            f"{label}: notify_owner's schema was taken off the desk; escalating "
            f"to the owner mid-sweep is a legitimate act"
        )
        assert line == "", (
            f"{label}: an opening line claimed a reply route: {line!r}"
        )
        return

    assert declared, f"{label}: nothing declared — the turn cannot be answered"
    overlap = set(declared) & set(suppressed)
    assert not overlap, (
        f"{label}: the reply reminder names {sorted(overlap)}, whose schema this "
        f"same turn removed"
    )
    assert declared[0] == expected_default, (
        f"{label}: the turn's default reply tool is {declared[0]}, expected "
        f"{expected_default} — origin-first ordering broke"
    )
    assert line, f"{label}: no opening line, so nothing tells the agent how to reply"
    assert f"`{expected_default}`" in line, (
        f"{label}: the opening line and the desk name different tools: {line}"
    )


@pytest.mark.asyncio
async def test_every_surface_gets_a_different_answer(monkeypatch):
    """The matrix is only meaningful if the surfaces are actually distinguished.

    Four rows that all produced the same desk would satisfy every assertion
    above while proving nothing — and "the same answer everywhere" is precisely
    the failure mode of reading the turn from stale instance state.
    """
    seen = []
    for label, source, extra, _ in SURFACES:
        declared, _sup = await _desk(
            _instances(), monkeypatch, working_source=source, extra=extra
        )
        seen.append((label, tuple(declared)))

    defaults = [d[0] if d else None for _lbl, d in seen]
    assert len(set(defaults)) == len(defaults), (
        f"two surfaces resolved to the same default reply tool: {seen}"
    )
