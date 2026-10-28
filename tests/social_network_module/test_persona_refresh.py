"""
@file_name: test_persona_refresh.py
@author: NarraNexus
@date: 2026-08-17
@description: When a contact's persona is worth re-inferring.

`should_update_persona` is the gate in front of an LLM call, so both of its
answers cost something real: a false "yes" burns a model round-trip on every
turn, a false "no" leaves the agent talking to who the contact used to be.

Ported from `src/.../social_network_module/test_persona.py`, a hand-run demo
script that pytest collected but `make test` never ran (it only runs `tests/`).
It had been asserting against `SocialNetworkModule._should_update_persona` —
a method that moved into `_entity_updater` long ago — so it could only ever
fail. The pure-logic half is worth keeping and lives here; the rest of that
script needed a live LLM key and a live database and was a demo, not a test.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.social_network_module._entity_updater import (
    should_update_persona,
)
from narranexus.platform.schema import SocialNetworkEntity


def _contact(**overrides) -> SocialNetworkEntity:
    fields = dict(
        instance_id="inst_test",
        entity_id="usr_123",
        entity_type="user",
        entity_name="Alice",
        persona="Terse, technical, wants numbers before narrative",
        interaction_count=5,
    )
    fields.update(overrides)
    return SocialNetworkEntity(**fields)


def test_a_contact_with_no_persona_yet_always_qualifies():
    """The first interaction is the one inference that cannot be skipped —
    there is nothing to fall back on."""
    assert should_update_persona(_contact(persona=None, interaction_count=0)) is True


def test_an_ordinary_turn_does_not_re_infer():
    """The default answer is no. Anything else puts an LLM call on the hot path
    of every single message."""
    assert should_update_persona(_contact(), "Hello, how are you?") is False


@pytest.mark.parametrize("turn", [10, 20, 100])
def test_every_tenth_turn_re_evaluates(turn):
    """Periodic refresh is what stops a stale persona from surviving forever
    once the contact stopped announcing their changes."""
    assert should_update_persona(_contact(interaction_count=turn)) is True


def test_turn_zero_is_not_mistaken_for_a_tenth_turn():
    """`0 % 10 == 0` is true, and a contact who already has a persona at turn 0
    must not be re-inferred on that arithmetic alone."""
    assert should_update_persona(_contact(interaction_count=0)) is False


@pytest.mark.parametrize(
    "utterance",
    [
        "Actually I care more about the pricing now",
        "I changed my mind about the timeline",
        "our requirements changed since last quarter",
    ],
)
def test_a_stated_change_beats_the_schedule(utterance):
    """The contact saying so is better evidence than a turn counter, so it does
    not wait for the next multiple of ten."""
    assert should_update_persona(_contact(interaction_count=3), utterance) is True


def test_the_change_signals_are_matched_case_insensitively():
    """Real messages are not lowercased before they get here."""
    assert should_update_persona(_contact(interaction_count=3), "I CHANGED MY MIND") is True


def test_an_empty_utterance_is_not_a_change_signal():
    """Callers pass "" when there is no response to look at; that is an absence
    of evidence, not evidence of change."""
    assert should_update_persona(_contact(interaction_count=3), "") is False
