"""
@file_name: test_visibility_wording.py
@author:
@date: 2026-08-11
@description: The module's standing rules must be true in every room it reaches.

`_static_instruction_parts` is emitted on every bus-enabled turn with no idea
what kind of room the turn is in — that is the point of it, and the R4 cache
depends on it staying byte-identical. So anything it asserts has to hold
everywhere, and two lines did not:

  * "In group channels, you only see messages that @mention you." True for an
    ordinary bus group. False in a team room, whose turn prompt carries the
    room's full recent scrollback and says so ten lines later. Two contradictory
    claims about the same room, in the same context window.
  * "Ignored messages resurface — they stay unread and appear again next turn."
    True in a DM, where the unread list is the queue. False in a team room since
    the read cursor started advancing on a rendered turn.

The fix is not a room-type branch — that would fork the static block and cost
the byte-stability it exists for. It is to say only what is true everywhere and
leave the room-specific fact to the room's own prompt, which is the only place
that knows.
"""
from __future__ import annotations

from narranexus.platform.message_bus.system_messages import SYSTEM_SENDER_LABEL
from narranexus_plugins.message_bus_module.message_bus_module import (
    MessageBusModule,
)


def _static_text() -> str:
    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    return "\n".join(module._static_instruction_parts())


def test_the_static_rules_do_not_claim_mentions_limit_visibility():
    """Activation and visibility are different questions; this block may only
    speak to the first."""
    text = _static_text().lower()

    assert "you only see messages that @mention you" not in text


def test_activation_semantics_are_still_stated():
    """The part that IS true everywhere has to survive the rewrite — it is what
    stops an agent assuming a passive post woke somebody."""
    text = _static_text()

    assert "only @-mentioned agents are activated" in text


def test_resurfacing_is_scoped_to_where_it_happens():
    """A team room now clears its cursor once a turn has rendered the room, so
    an unqualified "ignored messages come back" is a promise the platform stops
    keeping the moment the agent is in a team."""
    text = _static_text()

    # The wording moved off "resurface" (2026-08-17 rewrite); the GUARANTEE is
    # unchanged — the claim must name the surface it holds on, because a team
    # room clears its cursor once a turn has rendered it.
    line = next(
        ln for ln in text.splitlines()
        if "comes back" in ln.lower() or "resurface" in ln.lower()
    )
    low = line.lower()
    assert "private" in low or "direct" in low or "dm" in low
    assert "room works differently" in text


def test_a_teammate_is_marked_as_one_in_the_known_agents_list():
    """`via_team` was computed for every peer and read by nobody.

    The list mixes teammates with every other agent the owner has, and an agent
    reaching for help has no way to tell "we are on the same team, this one is
    already in the room with me" from "a stranger I would have to DM cold".
    """
    from narranexus.platform.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_known_agents"] = [
        {"agent_id": "agent_mate", "agent_name": "Mate",
         "agent_description": "OCR", "via_team": True},
        {"agent_id": "agent_stranger", "agent_name": "Stranger",
         "agent_description": "Unrelated", "via_team": False},
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    mate_line = next(ln for ln in text.splitlines() if "agent_mate" in ln)
    other_line = next(ln for ln in text.splitlines() if "agent_stranger" in ln)
    assert "teammate" in mate_line
    assert "teammate" not in other_line


def test_the_delivery_rule_does_not_claim_plain_text_reaches_nobody():
    """The loudest line in the block was also the one a team room contradicts.

    "Ending the turn with the result only as plain text delivers NOTHING" is
    true wherever the agent must call a delivery tool, and exactly backwards in
    a team room, whose turn prompt says the opposite in the same context
    window: the plain text IS the reply, and a delivery tool would double-post.
    """
    # Case-folded: a negative assertion that only catches the exact casing it
    # was written against is a guard the next edit walks straight through.
    text = _static_text().lower()

    assert "delivers nothing" not in text
    assert "only as plain text" not in text


def test_the_delivery_obligation_itself_survives():
    """Deleting the contradiction must not delete the P0 it was written for.

    2026-08-01: five agents did real research and ended their turns with the
    results as plain text on a surface that delivered none of it. The
    obligation stays; only the mechanism moves.
    """
    text = _static_text()

    assert "Finished work is never ping-pong" in text


def test_the_delivery_rule_defers_the_mechanism_to_the_surface():
    """Both ways of delivering have to be named, because both are real.

    The block cannot know which surface this turn is on, so it states the duty
    and points at the turn for the how — the same move the visibility rewrite
    above made.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "Finished work is never ping-pong" in ln
    )

    # 2026-08-17 — this used to require the tool NAMES in the line. It must not
    # any more: naming a send verb here would be the surface-specific claim P1
    # forbids (the verb that fits differs by surface). 2026-08-21 — the line no
    # longer implies only ONE call can deliver (capability follows the agent),
    # but it must still point at the turn for the how, instead of leaving
    # "deliver it" unactionable.
    assert "the top of the turn names the default one" in line
    assert "only the turn knows" in line


def test_the_delivery_rule_names_no_surface_specific_tool():
    """RETIRED-AND-REPLACED 2026-08-17.

    The old assertion required "do NOT also call a delivery tool" plus the team
    case first — a prohibition that existed because a team room auto-posted the
    agent's plain text, so calling a tool as well double-posted. Nothing
    auto-posts any more, so that hazard is gone and the prohibition would now be
    describing a mechanism that does not exist (P5).

    What replaces it is narrower than the sentence that used to sit here, which
    claimed "the block must not name a per-surface tool at all" while checking a
    single line. The block DOES name both send verbs — it has to, since it is
    where their disciplines are taught and it cannot branch on the turn (R4
    byte-stability). What it must not do is attach a per-surface tool to the
    GENERAL delivery rule, which is the line this checks: that rule applies to
    every surface, so naming one surface's verb in it is false on the others.

    The "exactly one per turn" half is pinned in test_bus_expressive_declaration
    (the desk) and by the test below (the text that has to admit it).
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "Finished work is never ping-pong" in ln
    )

    for surface_tool in ("message_team", "message_agent", "reply_owner"):
        assert surface_tool not in line, (
            f"{surface_tool} is surface-specific; naming it in the byte-stable "
            f"block is the P1 violation this file exists to catch"
        )

def test_the_block_does_not_confine_the_agent_to_the_trigger_channel():
    """The block teaches both verbs; the turn is not confined to one. It says so.

    2026-08-20 — capability follows the agent, not the trigger channel.
    `disallowed_tools` no longer removes the non-default verb's schema, so
    the block must tell the agent it is not confined to the conversation that
    woke it. The old dead-end wording ("finish this turn; a fresh one will have
    that call") was the prose face of the drop the redesign removed; asserting
    it is gone stops the reflex-arc framing from being quietly restored.

    Byte-stability (R4) still rules out branching, so the sentence carries a
    surface-blind hedge — 'unless this turn's own prompt says otherwise' — which
    is what keeps it TRUE on patrol (where both verbs are genuinely off the
    desk) without naming that surface. That hedge is asserted, not optional.
    """
    text = _static_text()
    low = text.lower()

    assert "you are not confined to it" in low, (
        "the block must state the agent is not confined to the conversation "
        "that woke it"
    )
    # The clause that keeps the sentence TRUE on patrol (where both send verbs
    # ARE off the desk) without branching on room type. Removing it reintroduces
    # a byte-stable claim that is false on one surface — the #311 defect class.
    assert "unless this turn's own prompt says otherwise" in low, (
        "the availability claim must be hedged so it holds on patrol too"
    )
    # The reflex-arc dead-ends must be gone, or the new capability is contradicted
    # a few lines from where it is granted.
    assert "exactly one of these two calls per turn" not in low
    assert "a fresh one will have that call" not in low


def test_no_rule_flatly_calls_the_counterparty_a_machine():
    """The species claim had a second copy, with the behaviour attached.

    Retracting "the message came from another agent, NOT from your owner" while
    leaving "The other party is another agent, not a human. Skip pleasantries"
    fourteen lines below keeps the harm and drops only its justification — the
    imperative half re-asserts the claim on its own authority, and a team
    room's owner posts over the bus.
    """
    text = _static_text()

    assert "The other party is another agent, not a human." not in text
    # The brevity half carries the ping-pong P0 and must survive the scoping.
    assert "Brevity beats politeness" in text
    # And the POSITIVE half: retracting the flat claim is only half the fix if
    # nothing routes a human sender out of the skip-pleasantries rule.
    assert "When the sender is a PERSON" in text
    # 2026-08-17 — the cross-reference is gone, so the dangling-pointer guard
    # goes with it: the rule now stands on its own, and the fact it used to point
    # at ("a sender shown as `User` is a PERSON") is asserted directly below.
    assert "is a **PERSON**, not an agent" in text


def test_the_block_infers_nothing_about_the_turn_from_an_absent_tag():
    """Twice now this line has guessed the turn's origin from a missing marker.

    v1 said "no tag ⇒ from your owner via the main chat interface" — which an
    IM turn contradicts in the same context window (`channel_prompts.py`) and a
    job turn has no human for at all. v2 said "no tag ⇒ did not arrive over the
    bus", which was false on 100% of bus turns: the input prefix it described
    never existed (dead guard, dead sink — see the module comment), so no turn's
    input has ever carried one. Same mistake, moved.

    The tags describe the unread QUEUE. What started the turn is the turn
    prompt's business, and this block may not claim otherwise in either
    direction.
    """
    text = _static_text()

    assert "is from your owner via the main chat interface" not in text
    assert "did not arrive over the bus" not in text
    assert "do not infer it from the presence or absence of a tag" in text


def test_no_rule_promises_a_tag_on_the_turns_input():
    """The prefix these rules described was never emitted.

    Its producer gated on `extra_data["working_source"]` (nothing writes that
    key — it is a ContextData FIELD) and wrote to `extra_data["input_content"]`
    (nothing reads that key — the input is the FIELD). Every rule pointing at
    "the start of the input" aimed the agent at a marker no turn carries, and
    the errand playbook's step 4 waited on it to close the loop back to the
    owner.
    """
    text = _static_text()

    assert "at the start of the input" not in text
    assert "beginning of user input" not in text
    assert "input tagged" not in text
    # Nor may it name WHERE the list lives: the R4 relocation flag can move
    # those lists into this very block, and with zero unreads the heading is
    # absent entirely.
    assert "MessageBus turn context" not in text
    # And the rules still describe the surface that DOES reach the model — the
    # unread list — so the tag the agent sees is one it was taught.
    assert "unread list is tagged" in text


def test_the_input_tagging_branch_is_gone_not_merely_unreachable():
    """Deleted, not left dormant behind a guard nobody can satisfy.

    A dead branch that still reads plausible is how the next reader concludes
    the mechanism exists — which is exactly what happened here, twice, to two
    separate rounds of this change.
    """
    import inspect

    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    # Code lines only — the comment left in its place NAMES both dead keys, to
    # stop the branch being helpfully reinstated by the next reader.
    code = "\n".join(
        ln for ln in
        inspect.getsource(MessageBusModule.gather).splitlines()
        if not ln.lstrip().startswith("#")
    )

    # Bare identifiers, not the exact call spellings: asserting on
    # `extra_data.get("working_source")` would be defeated by adding a default
    # argument, and `extra_data["input_content"]` by switching to `.get()`.
    # Neither name has any other legitimate use in this method today.
    reinstated = (
        "the retired input-source-tag branch must not be reinstated; "
        "see the comment at the end of gather"
    )
    assert "input_content" not in code, reinstated
    assert "working_source" not in code, reinstated

    # The DOCSTRING is checked against the raw source, deliberately.
    #
    # The comment-stripped view above cannot see it, and for four rounds the
    # method's own contract went on listing "5. prefix the input with a source
    # tag" as a step — the copy physically closest to the deleted code, and the
    # one this very test was written to make impossible. A guard that skips the
    # place the claim actually survived is not a guard.
    raw = inspect.getsource(MessageBusModule.gather)
    assert "prefix the input" not in raw
    # Paired with a POSITIVE, because the negative alone is one possessive away
    # from banning an honest historical sentence ("a step here used to prefix
    # the input…"). If someone rewords accurately, this is what should still
    # hold; if they reinstate the mechanism, this is what disappears.
    #
    # Whitespace-normalised: the phrase is wrapped across lines in the
    # docstring, and an assertion that a reflow can break is a guard that
    # retires itself the first time someone runs a formatter.
    assert "never to have executed" in " ".join(raw.split())

    # The MODULE header is the fourth copy, and until now the only one nothing
    # held: the prompt text has several negatives, this docstring has the two
    # above, the mirror is held by review. Re-adding the retracted line up there
    # would have left the suite green.
    from narranexus_plugins.message_bus_module import message_bus_module as mod

    assert "prefixed with [MessageBus" not in (mod.__doc__ or "")


def test_the_worked_example_is_the_tag_the_code_actually_emits():
    """The example taught a four-field tag; both render sites build three.

    Harmless while the tag was decoration. Once the rules say "read the sender
    in the tag", an agent following the example hunts for a display name that
    is never there and finds the CHANNEL in the position it was told holds an
    id. Generated from the same helper so the two cannot drift again.
    """
    from narranexus_plugins.message_bus_module.message_bus_module import (
        _bus_tag,
    )

    text = _static_text()

    # BOTH forms, because the tag now has two: a private conversation prints
    # the sender alone, a team room prefixes the team's name. An example that
    # showed only one would teach the agent to expect a field that is absent
    # half the time — the same defect as the four-field example, one shape on.
    assert _bus_tag("agent_xxx") in text
    assert _bus_tag("agent_xxx", "Ops") in text
    assert "[MessageBus · AgentName · agent_xxx · ch_yyy]" not in text
    # And no channel id may appear in the example, whatever its shape: the
    # field it used to occupy is a team NAME now.
    assert "ch_yyy" not in text


def test_the_unread_header_does_not_repeat_the_retracted_promise():
    """The same sentence, a hundred lines down, sitting on the list it is
    wrong about.

    The static rules were scoped to DMs because a team room clears its cursor
    once a turn has rendered it. The volatile block still printed the
    unqualified version — directly above an unread list that MIXES team-room
    messages in. Fixing one copy and leaving the other is how a contradiction
    survives a PR that was written to remove it.
    """
    from narranexus.platform.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_unread_messages"] = [
        {"from_agent": "agent_peer", "channel_id": "ch_1", "content": "ping"}
    ]

    text = "\n".join(module._volatile_context_parts(ctx))

    assert "Ignored messages stay unread" not in text
    assert "Reply Discipline" in text


def test_the_team_prompt_really_does_promise_what_the_static_rule_defers_to():
    """The cross-file contract that makes the rewritten rule TRUE.

    The static block now says "if this turn's prompt tells you your reply is
    posted for you, writing it IS delivering it". That sentence is only correct
    while the team prompt actually makes that promise and actually forbids the
    delivery tools. Nothing pinned that half, so a reword on the trigger side
    could quietly turn the module's rule back into a lie — which is the exact
    failure mode this whole file exists to catch, one file over.
    """
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    prompt = trigger._build_team_prompt(
        "agent_me",
        [],
        [{"agent_id": "agent_me", "name": "Me", "description": "", "capabilities": []}],
        owner_user_id="usr_1",
        team_id="team_x",
        trigger_messages=[],
        bulletin=None,
    )

    # 2026-08-17 — the promise itself changed, and the contract has to track it.
    #
    # The static rule used to defer to "your reply is posted for you"; it now
    # defers to "the send call for the conversation it belongs to". So what the
    # room must actually do is NAME that call. If the room stopped naming it, the
    # module's rule would be pointing at nothing — the exact failure this test
    # exists for, one file over.
    assert "message_team(" in prompt
    # And the room must still say what reaches it, because "deliver it" is only
    # actionable if the agent knows what counts as delivery here.
    assert "Nothing you write outside that call reaches the room" in prompt


def _unread_lines(rows: list[dict]) -> list[str]:
    from narranexus.platform.schema.context_schema import ContextData

    module = MessageBusModule.__new__(MessageBusModule)
    module.agent_id = "agent_me"
    ctx = ContextData(agent_id="agent_me", user_id="usr_1", input_content="hi")
    ctx.extra_data["bus_unread_messages"] = rows
    return "\n".join(module._volatile_context_parts(ctx)).splitlines()


def test_a_platform_line_is_labelled_not_quoted_as_the_owner():
    """A bulletin notice is stamped with the OWNER's id and is not the owner.

    Posted from the UI, `team_bulletin` records no actor, so `from_agent` is
    `usr_<owner>` while `msg_type` is a platform type. On the sender alone it
    renders as `User` — and the rules now say in as many words that a `User`
    sender is a PERSON and should be talked to like one. The type has to
    outrank the sender or the platform gets quoted as the human.
    """
    from narranexus.platform.message_bus.team_bulletin import BULLETIN_NOTICE_MSG_TYPE

    line = next(
        ln for ln in _unread_lines([{
            "from_agent": "usr_owner1", "channel_id": "ch_1",
            "content": "Team bulletin updated.",
            "msg_type": BULLETIN_NOTICE_MSG_TYPE,
        }]) if "bulletin updated" in ln
    )

    assert SYSTEM_SENDER_LABEL in line
    assert "User" not in line


def test_a_room_marker_sender_does_not_become_a_phantom_teammate():
    """`team_<id>` is the room speaking, and it resolves to no member.

    `message_bus_trigger._who` refuses to print it verbatim for exactly this
    reason: naming it invents a teammate the agent may then try to @mention
    back. The unread list was printing it raw.
    """
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE

    line = next(
        ln for ln in _unread_lines([{
            "from_agent": "team_abc123", "channel_id": "ch_1",
            "content": "Checking the board.", "msg_type": PATROL_MSG_TYPE,
        }]) if "Checking the board" in ln
    )

    assert SYSTEM_SENDER_LABEL in line
    assert "team_abc123" not in line


def test_an_ordinary_peer_and_an_ordinary_person_are_unaffected():
    """The labelling must not swallow the two senders it was built to name."""
    lines = _unread_lines([
        {"from_agent": "usr_a1b2c3", "channel_id": "ch_1",
         "content": "where are we", "msg_type": "text"},
        {"from_agent": "agent_peer", "channel_id": "ch_1",
         "content": "ping", "msg_type": "text"},
    ])

    human = next(ln for ln in lines if "where are we" in ln)
    peer = next(ln for ln in lines if "ping" in ln)
    assert "User" in human and "usr_a1b2c3" not in human
    assert "agent_peer" in peer
    assert SYSTEM_SENDER_LABEL not in human and SYSTEM_SENDER_LABEL not in peer


# ── Restored 2026-08-17 ─────────────────────────────────────────────────────
#
# These two were swept away as collateral when the input-source-tag test was
# removed with its branch (the deletion was done by slicing between two
# function names, and three unrelated tests sat inside the slice). Nothing
# took over their assertions, and the round that dropped them reported the
# file as "18 tests" — growth, not loss.
#
# What went unguarded meanwhile is not incidental: these pin change #3 and
# change #2 of this PR's own first commit, plus the M1 scoping from the
# second. A file whose entire purpose is "the static rules must be true in
# every room" cannot silently stop guarding the sentences the PR was opened
# to fix.


def test_silence_is_producing_nothing_not_merely_calling_nothing():
    """"Just stop the turn" is a tool-call instruction on a text-delivery surface.

    Where the reply auto-posts, ending the turn with any leftover text still
    sends a message — so "do not call the tool" does not add up to silence and
    the rule has to say what silence actually costs.
    """
    line = next(
        ln for ln in _static_text().splitlines()
        if "choose silence explicitly" in ln
    )

    # No trailing period in the negative: re-adding "Just stop the turn" with
    # any other punctuation would have sailed past the first version of this.
    assert "just stop the turn" not in line.lower()
    # And silence is toward the BUS. This same block obliges an owner relay
    # forty lines down ("never suppresses reporting back to your owner"), so an
    # unqualified "no reply text at all" can swallow the answer a person is
    # waiting for. The tightened phrase is the assertion — not the loose
    # "no reply text", which the pre-M1 wording also satisfied.
    # The wording moved (nothing auto-posts now, so "no reply text" stopped being
    # the useful form); the M1 GUARANTEE is what is pinned — silence toward the
    # peer or room must not read as silence toward the owner.
    assert "no send call to that conversation" in line
    assert "reporting to your owner is a separate act" in line
    # This rule's old tail went too: "the unread cursor advances appropriately"
    # was a third, vaguer account of a cursor the resurfacing rule states
    # precisely and scopes. Asserted HERE, on the rule it belonged to, so a
    # failure names the right sentence. Cursor semantics are room-specific, so
    # by this file's own invariant they may never appear in this block at all.
    assert "cursor advances" not in _static_text()


def test_the_bus_tag_is_not_claimed_to_mean_the_sender_is_a_machine():
    """A team room carries its owner's OWN messages over the bus.

    They reach the unread list as `usr_<id>`, so a flat "this came from another
    agent, NOT from your owner" is false exactly where a person is waiting for
    an answer, and it arrives attached to a rule that says to drop the
    pleasantries.
    """
    text = _static_text()

    assert "NOT from your owner" not in text
    # Wording moved off "the bus" (the agent is no longer told that word exists);
    # the guarantee is that a PERSON is named as possible.
    assert "A person can be in these conversations" in text


def test_the_platform_label_is_explained_not_just_rendered():
    """`[system]` in the list means nothing unless the rules say what it is.

    The renderer is guarded three ways over (platform type, room marker,
    ordinary senders), but the SENTENCE that tells the agent what a `[system]`
    entry is — and that it is not someone to answer or @mention — had nothing
    holding it. A label the agent cannot interpret is a label that gets
    answered.
    """
    text = _static_text()

    line = next(ln for ln in text.splitlines() if f"`{SYSTEM_SENDER_LABEL}`" in ln)
    assert "PLATFORM" in line
    assert "@mention" in line


def test_both_surfaces_name_a_platform_line_the_same_way():
    """The label is a cross-file agreement, so it gets a cross-file test.

    The team prompt's scrollback and this module's unread list render the SAME
    platform rows. They were two independent literals with the agreement stated
    only in a comment — and this PR added the second one while writing an
    invariant that says to sweep every copy of a piece of wording. A rename on
    one side would put the same notice under two names in one context window,
    and the rule that tells the agent what the label MEANS reads only one.

    Sibling of `test_the_team_prompt_really_does_promise_what_the_static_rule_
    defers_to`: same class of promise, same reason nothing else can hold it.
    """
    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
    from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE
    from narranexus.platform.message_bus.schemas import BusMessage

    trigger = MessageBusTrigger.__new__(MessageBusTrigger)
    prompt = trigger._build_team_prompt(
        "agent_me",
        [BusMessage(
            message_id="m1", channel_id="ch_1", from_agent="team_abc123",
            content="Checking the board.", msg_type=PATROL_MSG_TYPE,
        )],
        [{"agent_id": "agent_me", "name": "Me", "description": "", "capabilities": []}],
        owner_user_id="usr_1",
        team_id="team_x",
        trigger_messages=[],
        bulletin=None,
    )

    scrollback = next(ln for ln in prompt.splitlines() if "Checking the board" in ln)
    unread = next(
        ln for ln in _unread_lines([{
            "from_agent": "team_abc123", "channel_id": "ch_1",
            "content": "Checking the board.", "msg_type": PATROL_MSG_TYPE,
        }]) if "Checking the board" in ln
    )

    assert SYSTEM_SENDER_LABEL in scrollback
    assert SYSTEM_SENDER_LABEL in unread
