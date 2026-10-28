"""
@file_name: test_team_bulletin_prompt.py
@author: NarraNexus
@date: 2026-08-10
@description: The bulletin's contract with the prompt — the acceptance
criterion the whole feature exists for.

The PRD's complaint is precise: a rule the user stated once falls out of every
member's view as soon as 20 messages have gone by, because the 20-message
scrollback is a team turn's ONLY continuity. So the property to pin is not
"the bulletin appears somewhere" but "it appears regardless of what the
scrollback happens to contain" — including when the scrollback is empty, which
is exactly the state a newly-added member starts in.

Position is also load-bearing and asserted here. The bulletin goes BEFORE the
conversation: it is the standing constraint the messages are read under, and a
block appended after twenty lines of chat reads as a footnote to them.

The zero-cost case is a stated acceptance criterion, not an optimisation. An
empty bulletin that still emitted a header would put "[Team Bulletin]\\n(none)"
into every turn of every team that never uses the feature.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.schema.team_schema import BulletinEntry

# The builder takes a roster now (team-card change), same two members.
MEMBERS = [
    {"agent_id": "agent_a", "name": "Alice"},
    {"agent_id": "agent_b", "name": "Bob"},
]


def _entry(content, *, source="user", author_id="usr_1", tier="long_term", eid="bul_1"):
    return BulletinEntry(
        entry_id=eid,
        team_id="team_42",
        content=content,
        source=source,
        author_id=author_id,
        tier=tier,
    )


def _msg(content="hi", from_agent="agent_a"):
    return BusMessage(
        message_id="m1",
        channel_id="ch1",
        from_agent=from_agent,
        content=content,
        msg_type="text",
        created_at="2026-08-10T00:00:00Z",
    )


def _prompt(bulletin, history=None):
    return MessageBusTrigger(bus=None)._build_team_prompt(
        "agent_b",
        history if history is not None else [],
        MEMBERS,
        owner_user_id="user_a",
        team_id="team_42",
        bulletin=bulletin,
    )


# ── the acceptance criterion ────────────────────────────────────────────────


def test_a_rule_reaches_the_prompt_with_an_empty_scrollback():
    """The new-member case: nothing said in this room is visible to them yet."""
    out = _prompt([_entry("所有输出使用中文")], history=[])
    assert "所有输出使用中文" in out


def test_a_rule_reaches_the_prompt_when_the_scrollback_is_about_something_else():
    """The 20-messages-later case — the rule is not in the conversation at all."""
    chatter = [_msg(content=f"unrelated {i}") for i in range(20)]
    out = _prompt([_entry("所有输出使用中文")], history=chatter)
    assert "所有输出使用中文" in out


def test_every_entry_is_present_not_just_the_first():
    out = _prompt(
        [
            _entry("rule one", eid="bul_1"),
            _entry("rule two", eid="bul_2"),
            _entry("rule three", eid="bul_3"),
        ]
    )
    for c in ("rule one", "rule two", "rule three"):
        assert c in out


# ── zero cost when unused ───────────────────────────────────────────────────


def test_an_empty_bulletin_adds_nothing_at_all():
    """Stated acceptance criterion. Not even a header."""
    with_none = _prompt(None)
    with_empty = _prompt([])
    assert "Bulletin" not in with_empty
    assert with_none == with_empty


def test_an_unused_bulletin_leaves_the_prompt_byte_identical():
    """The regression that matters for every team not using the feature.

    "Byte-identical" is scoped to the BULLETIN's contribution: a team with no
    rules must get the same prompt whether the bulletin is absent (None, e.g. a
    failed read) or present-and-empty. It is not a claim that the prompt is
    unchanged from before the feature existed — the tool blurb is unconditional,
    and #259's work board adds a block of its own.
    """
    baseline = MessageBusTrigger(bus=None)._build_team_prompt(
        "agent_b",
        [],
        MEMBERS,
        owner_user_id="user_a",
        team_id="team_42",
        bulletin=None,
    )
    assert _prompt([]) == baseline


# ── position ────────────────────────────────────────────────────────────────


def test_the_bulletin_comes_before_the_conversation():
    """Standing rules are the frame the messages are read in. After the
    scrollback they read as a footnote to the chat instead."""
    out = _prompt([_entry("standing rule")], history=[_msg(content="chatter")])
    assert out.index("standing rule") < out.index("chatter")


# ── tiers and attribution ───────────────────────────────────────────────────


def test_the_current_task_entries_are_marked_as_such():
    """A per-task input path must not read as a permanent policy."""
    out = _prompt(
        [
            _entry("permanent policy", tier="long_term", eid="bul_1"),
            _entry("this task's input is /x.csv", tier="current_task", eid="bul_2"),
        ]
    )
    assert "permanent policy" in out and "this task's input is /x.csv" in out
    assert out.index("permanent policy") < out.index("this task's input is /x.csv")


def test_an_agent_written_entry_is_attributed():
    """The user needs to see which rules the team invented for itself, because
    those are the ones they may want to remove."""
    out = _prompt([_entry("we agreed on v2", source="agent", author_id="agent_a")])
    assert "Alice" in out


def test_a_user_written_entry_is_not_attributed_to_an_agent():
    out = _prompt([_entry("boss says", source="user", author_id="usr_1")])
    assert "Alice" not in out.split("Recent messages")[0].split("[Team")[-1]


# ── the summary is flagged as guesswork ─────────────────────────────────────


def test_the_summary_is_labelled_as_automatic_and_possibly_stale():
    """It is best-effort machine output sitting next to rules a human typed.
    Unlabelled, an agent has no way to weigh one against the other."""
    out = _prompt([_entry("progress so far", source="auto_summary", author_id=None)])
    assert "progress so far" in out
    lowered = out.lower()
    assert "auto" in lowered and ("stale" in lowered or "lag" in lowered)


def test_the_summary_is_separated_from_the_rules():
    """A summary rendered as rule N would be obeyed as an instruction."""
    out = _prompt(
        [
            _entry("obey this", source="user", eid="bul_1"),
            _entry("the team is halfway", source="auto_summary", author_id=None, eid="bul_2"),
        ]
    )
    assert out.index("obey this") < out.index("the team is halfway")


# ── the wiring, not just the renderer ───────────────────────────────────────
#
# A renderer nobody calls is dead code that passes its own tests. These cover
# the seam between the DB read and the pure function above — the exact kind of
# gap that previously shipped a NameError on the path every team turn takes.


def test_the_dispatch_site_passes_the_bulletin_into_the_prompt():
    """Both halves exist and are connected: the loader is called on the team
    branch, and its result is handed to the renderer."""
    import inspect

    from narranexus.platform.message_bus import message_bus_trigger as mod

    src = inspect.getsource(mod.MessageBusTrigger._handle_channel_batch)
    assert "_load_bulletin(" in src
    assert "bulletin=bulletin" in src


@pytest.mark.asyncio
async def test_an_unreadable_bulletin_degrades_instead_of_killing_the_turn(monkeypatch):
    """Losing the standing rules is a degradation; losing the reply is an
    outage. The turn is still answerable without the bulletin."""
    from narranexus.platform.message_bus import message_bus_trigger as mod

    async def boom():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(mod, "logger", mod.logger)
    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", boom, raising=True)

    got = await MessageBusTrigger(bus=None)._load_bulletin("team_42")
    assert got == []


@pytest.mark.asyncio
async def test_a_failed_read_is_logged_rather_than_swallowed(monkeypatch):
    """Returning [] silently would present an unreachable database as "this
    team has no rules" — the user would see their rules ignored and have
    nothing anywhere saying why."""
    from narranexus.platform.message_bus import message_bus_trigger as mod

    async def boom():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", boom, raising=True)

    seen = []
    monkeypatch.setattr(mod.logger, "warning", lambda m: seen.append(str(m)))

    await MessageBusTrigger(bus=None)._load_bulletin("team_42")
    assert any("team_42" in m for m in seen)


# ── every path that speaks in the room loads the rules ──────────────────────
#
# Merging #259 created a SECOND caller of _build_team_prompt (the patrol sweep),
# and `bulletin` being optional turned that into a silent off switch: the whole
# rules block vanished from patrol turns with no error and no log.
#
# Patrol is not internal bookkeeping. It is a real LLM turn whose reply is posted
# into the same room with @mentions, so a rule the user pinned bound every member
# who was @mentioned and did NOT bind the Leader the platform woke — while the
# tool blurb in the same prompt still told it the bulletin loads every turn. That
# is the direct negation of this feature's first acceptance criterion.
#
# The parameter is now required, so the omission is no longer expressible.


def test_the_bulletin_parameter_is_required():
    """A caller that forgets it should fail loudly rather than quietly produce a
    prompt with no rules in it. That is the difference between a bug someone
    notices immediately and one that ships."""
    import inspect

    sig = inspect.signature(MessageBusTrigger._build_team_prompt)
    assert sig.parameters["bulletin"].default is inspect.Parameter.empty, (
        "bulletin is optional again — a new call site can now drop the rules silently"
    )


def test_omitting_the_bulletin_is_a_hard_error():
    """The property, not the spelling.

    This used to count substrings in the module source, which verified my own
    formatting rather than anything about behaviour: renaming a local would have
    broken it, and a new call site passing `bulletin=None` explicitly would have
    satisfied it while dropping every rule. What actually matters is that a
    caller cannot omit the argument at all.
    """
    with pytest.raises(TypeError):
        MessageBusTrigger(bus=None)._build_team_prompt(
            "agent_b", [], MEMBERS, owner_user_id="user_a", team_id="team_42",
        )


def test_the_patrol_path_loads_the_bulletin():
    """The specific caller that was missing it."""
    import inspect

    from narranexus.platform.message_bus import message_bus_trigger as mod

    body = inspect.getsource(mod.MessageBusTrigger._patrol_body)
    assert "_load_bulletin(" in body
    assert "bulletin=bulletin" in body


# ── platform lines are not conversation ─────────────────────────────────────


def _sys(content, msg_type, from_agent="agent_a"):
    return BusMessage(
        message_id=f"s_{msg_type}", channel_id="ch1", from_agent=from_agent,
        content=content, msg_type=msg_type, created_at="2026-08-11T00:00:00Z",
    )


def test_platform_lines_are_labelled_not_attributed_to_a_member():
    """The property is "not dressed as conversation", NOT "absent".

    The first version of this test asserted the content did not appear, which
    pinned the wrong behaviour in place: dropping the line also removed the
    question a patrol chase asks, and the pointer line below it prints only the
    sender. Content stays; the impersonation is what goes.
    """
    from narranexus.platform.message_bus.system_messages import PLATFORM_MSG_TYPES

    history = [_msg(content="real work happened")]
    history += [_sys(f"platform said {t}", t) for t in PLATFORM_MSG_TYPES]

    out = _prompt([], history=history)

    assert "real work happened" in out
    for t in PLATFORM_MSG_TYPES:
        assert f"platform said {t}" in out, f"{t} content was dropped"
        assert f"Alice: platform said {t}" not in out, f"{t} rendered as a member"
        assert f"[system] platform said {t}" in out


def test_a_patrol_marker_never_reaches_the_transcript_as_a_name():
    """Its sender is a synthetic `team_<id>`, which member_map cannot resolve,
    so it would print raw."""
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE

    out = _prompt(
        [],
        history=[_sys("chasing you", PATROL_MSG_TYPE, from_agent="team_team_42")],
    )
    assert "team_team_42" not in out


def test_ordinary_messages_still_render():
    """The filter must key on msg_type, not on anything that would also drop
    real conversation."""
    out = _prompt([], history=[_msg(content="hello"), _msg(content="world")])
    assert "hello" in out and "world" in out


def test_a_patrol_chase_still_shows_what_it_asked():
    """The regression the platform-line filter created.

    A patrol reply is posted WITH @mentions (the Leader is told to "@mention the
    owner and ask where it stands"), so it becomes the mentioned member's
    trigger message for that turn. The prompt's pointer line prints only the
    sender — "You were just @mentioned by X. Respond to that message." — on the
    explicit grounds that the text "is already in the history above". Dropping
    platform lines from the history took the question away and left the pointer
    aimed at nothing: the agent answers the air, or invents what was asked.

    Stop and bulletin notices were never at risk (both send mentions=None and so
    can never be a trigger), which means the ONE sender this filter existed to
    silence is also the only one it broke.
    """
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE

    chase = _sys(
        "the parser task has been open for three days — where does it stand?",
        PATROL_MSG_TYPE,
        from_agent="team_team_42",
    )
    out = MessageBusTrigger(bus=None)._build_team_prompt(
        "agent_b", [chase], MEMBERS,
        owner_user_id="user_a", team_id="team_42",
        trigger_messages=[chase], bulletin=None,
    )

    assert "where does it stand?" in out, (
        "the agent is told to respond to a message the prompt does not contain"
    )
    # The other half of the same fix, and the half that had no assertion at all:
    # the pointer line names the sender, and a patrol line's sender is the
    # synthetic `team_<id>` marker. Printed verbatim it invents a teammate the
    # agent may try to @mention back. Deleting that whole branch used to leave
    # every test green.
    assert "team_team_42" not in out
    assert "the team's Leader check" in out


def test_a_platform_line_is_labelled_rather_than_impersonating_a_member():
    """Kept, but not dressed as conversation. The two faults being avoided are
    "Alice: Team bulletin updated." and a `team_<id>` marker appearing as if it
    were a member."""
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE

    out = _prompt(
        [],
        history=[_sys("chasing you", PATROL_MSG_TYPE, from_agent="team_team_42")],
    )
    assert "chasing you" in out
    assert "team_team_42" not in out
    assert "Alice: chasing you" not in out
