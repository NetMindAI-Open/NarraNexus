"""
@file_name: test_agent_description_unset.py
@author: NarraNexus
@date: 2026-08-04
@description: "This agent has no description yet" must be one shared judgement.

P1 section 02 (prod, 2026-08-03): every agent ever created carried
``agent_description = "A new agent ready for configuration"`` — creation wrote
it, nothing ever replaced it, and three surfaces then repeated it as if it were
a fact:

  * ``bus_agent_registry`` snapshots it → ``bus_get_agent_profile`` answers
    "a new agent ready for configuration" for a fully configured agent, so the
    ASKING agent concluded the peer was unconfigured and refused to send
    (evt_feb1f6ae). 488 registry rows, all placeholder.
  * the Known Agents list injected into every bus turn shows the same string
    for every peer, so "ask the teaching expert" has nothing to aim at.
  * BasicInfo injects it as the agent's OWN self-description, so the asked
    agent reads "I am a new agent ready for configuration" too.

Creation no longer writes the placeholder, but 488 prod rows carry it, so
"unset" has to recognise both the empty and the legacy form. One helper, so a
surface cannot accidentally treat the legacy string as real prose.
"""
from __future__ import annotations

import pytest

from narranexus.platform.schema import (
    LEGACY_AGENT_DESCRIPTION_PLACEHOLDER,
    is_agent_description_unset,
)


@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    LEGACY_AGENT_DESCRIPTION_PLACEHOLDER,
    "  A new agent ready for configuration  ",   # padded legacy row
    "a new agent ready for configuration",       # casing must not rescue it
])
def test_unset_forms(value):
    assert is_agent_description_unset(value) is True


@pytest.mark.parametrize("value", [
    "Teaching expert: curriculum design and lesson review.",
    "Runs the nightly cost report.",
    "New Agent",  # a NAME is not a description, but it is real prose here
])
def test_real_descriptions_are_kept(value):
    assert is_agent_description_unset(value) is False


def test_the_legacy_string_is_stated_once():
    """If this constant drifts from what creation used to write, 488 prod rows
    silently become 'real descriptions' again."""
    assert LEGACY_AGENT_DESCRIPTION_PLACEHOLDER == "A new agent ready for configuration"


def test_creation_no_longer_writes_the_placeholder():
    """The route must not reintroduce it — the constant exists to RECOGNISE
    legacy rows, not to keep writing them."""
    from pathlib import Path

    source = Path("backend/routes/auth.py").read_text(encoding="utf-8")
    assert 'or "A new agent ready for configuration"' not in source
