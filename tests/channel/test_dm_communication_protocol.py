"""
@file_name: test_dm_communication_protocol.py
@author:
@date: 2026-08-06
@description: Pins the DM-vs-group split of the IM Communication Protocol.

The shared channel prompt used to inject ONE Communication Protocol into
every channel turn regardless of room type: "Your default action is NO
REPLY", "When to Stay Silent (most of the time)", "When to Reply (rare)",
plus group-chat and @mention discipline. That rule set was tuned in
2026-03 for three GROUP problems (agent-to-agent ack loops, every group
message waking every member, @mention abuse) — applying it to a 1:1 DM
from a real person made silence the correct behaviour, which is the
0802 WeChat report: a person sends "hello" and the agent answers nothing.

WeChat is the extreme case: its context builder hard-codes room_type to
"Direct Message" (personal-account v1 is DM-only), so 100% of its turns
were being told not to reply.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.channel_prompts import (
    CHANNEL_MESSAGE_EXECUTION_TEMPLATE,
    COMMUNICATION_PROTOCOL_DIRECT,
    COMMUNICATION_PROTOCOL_GROUP,
    ROOM_TYPE_DIRECT,
    ROOM_TYPE_GROUP,
    communication_protocol_for,
)

# Phrases that make silence the default. Legitimate in a group, corrosive
# in a 1:1 DM.
GROUP_ONLY_MARKERS = (
    "Your default action is NO REPLY.",
    "### When to Stay Silent (most of the time)",
    "### When to Reply (rare)",
    "### Group Chat Rules",
    "### @Mention Discipline",
    "Being @mentioned does NOT obligate you to reply.",
)

# Discipline that stays true in BOTH room types — brevity and no
# performative reporting are not group-specific.
SHARED_MARKERS = (
    "Be brief.",
    "No performative reporting.",
)


class TestRoomTypeConstants:
    """room_type was an accidental enum — six builders each spelled the
    two literals by hand. Constants make it a real one."""

    def test_constants_keep_the_historical_wire_values(self):
        # These strings are rendered into the prompt ("Conversation Type")
        # and are what every existing builder already emitted.
        assert ROOM_TYPE_DIRECT == "Direct Message"
        assert ROOM_TYPE_GROUP == "Group Room"


class TestProtocolSelection:
    def test_direct_message_gets_the_direct_protocol(self):
        assert communication_protocol_for(ROOM_TYPE_DIRECT) == COMMUNICATION_PROTOCOL_DIRECT

    def test_group_room_gets_the_group_protocol(self):
        assert communication_protocol_for(ROOM_TYPE_GROUP) == COMMUNICATION_PROTOCOL_GROUP

    @pytest.mark.parametrize("unknown", ["", None, "Thread", "direct message"])
    def test_unknown_room_type_falls_back_to_group(self, unknown):
        """Conservative default: an unrecognised room type keeps the
        stricter discipline. Being too quiet in an unknown room type is
        recoverable; spraying replies into a 500-person group is not."""
        assert communication_protocol_for(unknown) == COMMUNICATION_PROTOCOL_GROUP


class TestDirectProtocolContent:
    def test_direct_protocol_drops_every_silence_default(self):
        for marker in GROUP_ONLY_MARKERS:
            assert marker not in COMMUNICATION_PROTOCOL_DIRECT, (
                f"1:1 DM protocol must not carry group discipline: {marker!r}"
            )

    def test_direct_protocol_makes_replying_the_default(self):
        assert "Replying is the default." in COMMUNICATION_PROTOCOL_DIRECT

    def test_direct_protocol_keeps_a_narrow_silence_carve_out(self):
        """Owner decision (2026-08-06): keep a narrow carve-out rather
        than forcing a reply to every "谢谢" — but it must be scoped to
        pure acknowledgment with nothing to add."""
        assert "pure acknowledgment" in COMMUNICATION_PROTOCOL_DIRECT

    def test_direct_protocol_keeps_the_shared_style_rules(self):
        for marker in SHARED_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_DIRECT


class TestGroupProtocolUnchanged:
    def test_group_protocol_keeps_all_discipline(self):
        """The group path is the tuned 2026-03 rule set — this change must
        not weaken it."""
        for marker in GROUP_ONLY_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_GROUP
        for marker in SHARED_MARKERS:
            assert marker in COMMUNICATION_PROTOCOL_GROUP


class TestTemplateWiring:
    def test_template_takes_the_protocol_as_a_placeholder(self):
        assert "{communication_protocol}" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE

    def test_template_no_longer_hardcodes_group_discipline(self):
        """If the protocol text stayed inline, the placeholder would be
        dead and every room type would keep getting group rules."""
        for marker in GROUP_ONLY_MARKERS:
            assert marker not in CHANNEL_MESSAGE_EXECUTION_TEMPLATE

    def test_template_keeps_the_reply_tool_contract(self):
        """Independent of room type: the sender only sees a reply that
        went through the channel reply tool. This is the other half of
        the 0802 bug and must survive the split."""
        assert "{reply_instruction}" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE
        assert "This is the ONLY way the sender will see your response" in (
            CHANNEL_MESSAGE_EXECUTION_TEMPLATE
        )

    def test_template_keeps_the_three_delivery_routes(self):
        """Guards the Bug-23 path rules that
        tests/channel/test_channel_prompts_path_rules.py also pins —
        asserted here so a careless protocol split fails fast."""
        assert "## File & Path Rules for IM Delivery" in CHANNEL_MESSAGE_EXECUTION_TEMPLATE


class TestDirectProtocolLoopBreaker:
    """2026-08-25, after the 8/14 ping-pong incident.

    The group protocol has had a loop-breaker since 2026-03. The DM
    protocol never did — it was written to cure the OPPOSITE failure
    (0802: too much silence) — so on the one room type where two agents
    can be alone together, the model was told replying is the default and
    given no exit. Two agents recited at each other for 70+ hours.
    """

    def test_direct_protocol_now_has_a_loop_breaker(self):
        assert "### Breaking a Loop" in COMMUNICATION_PROTOCOL_DIRECT

    def test_loop_breaker_names_repetition_as_the_trigger(self):
        text = COMMUNICATION_PROTOCOL_DIRECT
        assert "the same thing is being said again and again" in text
        assert "STOP. Do not reply." in text

    def test_repetition_only_counts_once_you_have_already_answered(self):
        """The 0802 shape, reachable through the back door.

        A person whose first message went unanswered resends "在吗" / "?" —
        which literally "repeats what was already sent". A trigger phrased
        that way punishes exactly the behaviour that means someone is
        still waiting, and the more they wait the more it silences. The
        trigger has to be "repeats something you ALREADY ANSWERED".
        """
        text = COMMUNICATION_PROTOCOL_DIRECT
        assert "you have already answered" in text
        assert "If they are repeating because they got no answer, answer them." in text

    def test_the_circles_declaration_has_an_exit(self):
        """"Stay silent even if more messages arrive" would let one
        "we're going in circles" line mute a DM permanently — including
        for a brand-new question."""
        text = COMMUNICATION_PROTOCOL_DIRECT
        assert "until there is something new" in text
        assert "even if more messages arrive" not in text

    def test_the_narrow_carve_out_no_longer_claims_to_be_exclusive(self):
        """`That is the whole carve-out.` sits ABOVE the new section and
        used to assert exhaustively, so a model resolving the conflict
        could rule the new exceptions out."""
        text = COMMUNICATION_PROTOCOL_DIRECT
        assert "That is the whole carve-out." not in text
        assert "still going somewhere" in text

    def test_loop_breaker_covers_a_machine_on_the_far_side(self):
        """"They are waiting and will think I'm broken" — the reason
        replying is the default — is simply false when nobody is waiting."""
        assert "read as machine-generated" in COMMUNICATION_PROTOCOL_DIRECT

    def test_loop_breaker_does_not_reinstate_the_group_defaults(self):
        """The whole risk of putting silence language back into the DM
        protocol is regressing 0802. It must stay a carve-out, never a
        default."""
        for marker in GROUP_ONLY_MARKERS:
            assert marker not in COMMUNICATION_PROTOCOL_DIRECT
        assert "Replying is the default." in COMMUNICATION_PROTOCOL_DIRECT
        assert "This does NOT weaken the default above." in COMMUNICATION_PROTOCOL_DIRECT

    def test_the_narrow_acknowledgment_carve_out_survives(self):
        """The 2026-08-06 owner decision (silence is right for a bare
        "谢谢") must not be swallowed by the new section."""
        assert "pure acknowledgment" in COMMUNICATION_PROTOCOL_DIRECT

    def test_group_protocol_is_untouched(self):
        """This PR must not edit the tuned 2026-03 group rule set."""
        assert "### Breaking a Loop" not in COMMUNICATION_PROTOCOL_GROUP
        assert "you are in a loop, STOP" in COMMUNICATION_PROTOCOL_GROUP


class TestModulePromptsDoNotContradictTheProtocol:
    """A channel module's own prompt sits in the same context window as the
    Communication Protocol and is more "local" to the model, so an
    unconditional module-level instruction can quietly override the shared
    rule. 8/14 happened on NarraMessenger, whose module prompt said "in
    direct messages, every message is for you — reply normally" with no
    exception clause at all.

    The fix is a REFERENCE, never a copy: duplicating the loop rule into
    module prompts guarantees the two drift (this repo already paid that
    tax once when static bus rules contradicted the team-room prompt).
    """

    def test_narramessenger_dm_instruction_defers_to_the_protocol(self):
        from narranexus_plugins.narramessenger_module import (
            narramessenger_module as nm,
        )

        behaviour = nm._BEHAVIOUR
        assert "Breaking a Loop" in behaviour, (
            "the module's unconditional 'reply normally' would override the "
            "shared protocol on the very channel the incident happened on"
        )

    def test_the_module_prompt_only_references_and_does_not_copy(self):
        """If the rule text itself is duplicated, the next protocol edit
        silently leaves a stale copy behind."""
        from narranexus_plugins.narramessenger_module import (
            narramessenger_module as nm,
        )

        assert "STOP. Do not reply." not in nm._BEHAVIOUR
        assert "going in circles" not in nm._BEHAVIOUR
