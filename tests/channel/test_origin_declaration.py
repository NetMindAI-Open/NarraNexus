"""
@file_name: test_origin_declaration.py
@date: 2026-08-17
@description: The one line that tells the agent where a turn came from
(design §6.1) is rendered from the registry, never phrased twice.

What these tests defend is not the wording — it is the property that made the
wording safe to write once: the label and the reply tool come from data the
platform already computed for other purposes (`MessageSourceRegistry`, and the
turn's `expressive_tools` output). Nothing here re-derives either, so the
sentence cannot end up describing a desk the agent does not have.

The prose this replaced said the same thing in each trigger's own words, and
those copies drifted — a channel prompt still naming a tool the desk no longer
carried gave the agent two instructions and no tiebreak.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.message_source_handler import (
    MessageSourceHandler,
    MessageSourceRegistry,
    render_origin_declaration,
)


def test_names_the_first_declared_tool_as_the_default():
    """Declaration order is contract: the first entry is the turn's default."""
    line = render_origin_declaration(
        "chat", ("mcp__chat_module__reply_owner",)
    )
    assert "`mcp__chat_module__reply_owner`" in line
    assert "NarraNexus" in line


def test_extra_tools_are_offered_but_not_confused_with_the_default():
    line = render_origin_declaration(
        "message_bus",
        ("mcp__message_bus_module__message_team", "mcp__chat_module__notify_owner"),
    )
    # The default reads before the parenthetical, so a model scanning the line
    # top-to-bottom meets the intended tool first — the flat-list failure mode
    # (2026-08-13: models followed a flat list over the per-message
    # instruction on 12/14 turns) is what the ordering exists to avoid.
    assert line.index("message_team") < line.index("notify_owner")
    assert "also available" in line


def test_no_declared_surface_says_nothing_at_all():
    """A turn with no reply surface must not be handed an invented one.

    Silence is the only safe output here: naming a tool the desk does not carry
    is precisely the failure the declaration exists to prevent, and it would be
    THIS function's own fault rather than a drifted copy somewhere else.
    """
    assert render_origin_declaration("chat", ()) == ""
    assert render_origin_declaration("lark", None) == ""


def test_unknown_source_still_renders_rather_than_raising():
    """An unregistered source falls back to the default handler.

    A source nobody registered is a gap in coverage, not a reason to blow up
    mid-turn — the agent still has a real desk and still needs to be told what
    answers it.
    """
    line = render_origin_declaration("some_future_source", ("mcp__x__y",))
    assert "`mcp__x__y`" in line


@pytest.mark.parametrize(
    "source,expected",
    [
        ("message_bus", "NarraNexus"),
        ("job", "NarraNexus (scheduled job)"),
        ("lark", "Lark"),
        ("wechat", "WeChat"),
        ("narramessenger", "NarraMessenger"),
    ],
)
def test_labels_speak_the_agent_s_two_situations(source, expected):
    """Labels are agent-facing, so they use the agent's model of the world.

    The harness teaches exactly two social situations — inside NarraNexus, or
    on an external IM channel. So everything internal says "NarraNexus" (the
    bus is infrastructure the agent never sees; naming it would introduce a
    third concept for nothing) and each channel says its own brand, cased the
    way that brand is actually written.
    """
    # No imports needed: the labels come from what the process BOOTED
    # (``ingress.channels`` descriptors + ``ingress.message_sources`` specs),
    # not from whichever plugin modules happen to have been imported.
    assert MessageSourceRegistry.get(source).label == expected


def test_label_falls_back_to_the_source_name():
    """An unlabelled handler still produces something readable."""
    assert MessageSourceHandler(name="matrix", user_reply_tool_names=()).label == "Matrix"
    assert (
        MessageSourceHandler(name="some_source", user_reply_tool_names=()).label
        == "Some Source"
    )


def test_the_declaration_and_the_desk_read_the_same_tuple():
    """The anti-drift property, stated as a test.

    `render_origin_declaration` is handed the SAME tuple the modules declared
    and `disallowed_tools` enforced — it does not look tools up for itself.
    So a tool that is not on the desk cannot appear in the sentence, no matter
    what the registry says about the source.
    """
    desk = ("mcp__chat_module__notify_owner",)
    line = render_origin_declaration("lark", desk)
    assert "reply_owner" not in line
    # lark's registry entry lists its channel tools, and none of them leak in
    # from there — only the desk decides.
    assert "lark_cli" not in line
