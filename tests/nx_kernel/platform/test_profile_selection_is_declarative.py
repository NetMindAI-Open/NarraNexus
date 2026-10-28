"""
@file_name: test_profile_selection_is_declarative.py
@author: Bin Liang
@date: 2026-09-07
@description: A profile says WHEN it applies as data (``when`` + ``order``), so a plugin profile is selectable without a platform edit.

Round-2 A2-7: registering a profile was an open slot, but SELECTING one was an
if-chain in ``platform/turn/pipeline.py`` naming the five builtin ids — so a
plugin's ``research`` profile could only ever run if the caller named it
explicitly, and "when does my profile apply?" had no declarative answer.
Restore the if-chain and ``test_a_plugin_profile_wins_a_discord_turn`` /
``test_lowest_order_wins_among_matches`` go red; the builtin table below is the
behaviour-preserving half (the five clauses ARE the old chain).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus.contracts.agent.pipeline import (
    PipelineProfile,
    WhenSyntaxError,
    evaluate_when,
)
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.turn import resolve_profile, turn_when_context


@pytest.fixture
def regs() -> Registries:
    r = Registries()
    load_builtins(r, "backend")
    return r


def _pick(regs, **kw) -> str:
    args = dict(fast_mode=False, silent=False, turn_profile=None, working_source="chat")
    args.update(kw)
    return resolve_profile(registries=regs, **args).id


# --- the grammar -------------------------------------------------------------

def test_the_grammar_covers_truthiness_negation_comparison_and_or():
    ctx = {"source": "discord", "fast_mode": True, "silent": False}
    assert evaluate_when("source == 'discord'", ctx)
    assert not evaluate_when("source == 'job'", ctx)
    assert evaluate_when("source != 'job'", ctx)
    assert evaluate_when("fast_mode", ctx)
    assert evaluate_when("!silent", ctx)
    assert evaluate_when("fast_mode and source == 'discord'", ctx)
    assert not evaluate_when("silent and source == 'discord'", ctx)
    assert evaluate_when("silent or fast_mode", ctx)
    # An unknown key is falsy, never an error: the host may add facts later.
    assert not evaluate_when("nonexistent_fact", ctx)


def test_an_empty_clause_never_matches():
    """The opposite of the frontend's "empty always holds": here that would be a
    profile silently winning every turn."""
    assert not evaluate_when("", {"source": "chat"})


def test_a_typo_is_a_parse_error_not_a_silent_never():
    with pytest.raises(WhenSyntaxError):
        evaluate_when("source = 'discord'", {"source": "discord"})
    with pytest.raises(WhenSyntaxError):
        evaluate_when("source.startswith('d')", {"source": "discord"})


# --- the turn fact bag -------------------------------------------------------

def test_the_fact_bag_is_the_contract_with_profile_authors():
    tp = SimpleNamespace(name="voice_fast", narrative_strategy="bm25_top1")
    ctx = turn_when_context(fast_mode=False, silent=True, turn_profile=tp, working_source="discord")
    assert ctx == {
        "silent": True,
        "fast_mode": False,
        "voice": True,
        "narrative_strategy": "bm25_top1",
        "source": "discord",
    }
    # No TurnProfile → no voice / no strategy, rather than a stray default.
    bare = turn_when_context(fast_mode=True, silent=False, turn_profile=None, working_source=SimpleNamespace(value="job"))
    assert bare["voice"] is False and bare["narrative_strategy"] == "" and bare["source"] == "job"


# --- behaviour preservation: the five builtin clauses ARE the old if-chain ---

def test_the_builtin_table_is_unchanged(regs):
    tp = SimpleNamespace(name="chat_fast", narrative_strategy="bm25_top1")
    voice = SimpleNamespace(name="voice_fast", narrative_strategy="bm25_top1")
    assert _pick(regs, silent=True) == "silent"
    assert _pick(regs, turn_profile=voice) == "voice"
    assert _pick(regs, turn_profile=tp) == "fast"
    assert _pick(regs, fast_mode=True, working_source="lark") == "fast"
    assert _pick(regs, working_source="job") == "job"
    assert _pick(regs) == "default"
    # silent still short-circuits everything below it
    assert _pick(regs, silent=True, fast_mode=True, working_source="job") == "silent"


def test_explicit_still_outranks_every_clause(regs):
    assert _pick(regs, silent=True, explicit="job") == "job"


# --- the point of the change -------------------------------------------------

def test_a_plugin_profile_wins_a_discord_turn(regs):
    """No platform edit: the plugin ships the condition as data."""
    regs.registry_for("turn.profiles").register_contribution(
        Contribution(
            "acme_discord",
            lambda: PipelineProfile(id="acme_discord", when="source == 'discord'", order=25),
        ),
        owner="acme.discord",
    )
    assert _pick(regs, working_source="discord") == "acme_discord"
    # ...and only for that source.
    assert _pick(regs, working_source="chat") == "default"


def test_lowest_order_wins_among_matches(regs):
    reg = regs.registry_for("turn.profiles")
    reg.register_contribution(
        Contribution("acme_broad", lambda: PipelineProfile(id="acme_broad", when="!silent", order=99)),
        owner="acme.x",
    )
    reg.register_contribution(
        Contribution("acme_narrow", lambda: PipelineProfile(id="acme_narrow", when="source == 'discord'", order=5)),
        owner="acme.x",
    )
    assert _pick(regs, working_source="discord") == "acme_narrow"
    assert _pick(regs, working_source="chat") == "acme_broad"
    # A specific plugin clause can even outrank a builtin one — that is the
    # point of exposing ``order`` rather than hiding the precedence in code.
    assert _pick(regs, working_source="discord", fast_mode=True) == "acme_narrow"


def test_a_profile_without_a_when_never_wins_by_itself(regs):
    regs.registry_for("turn.profiles").register_contribution(
        Contribution("acme_manual", lambda: PipelineProfile(id="acme_manual", order=1)),
        owner="acme.x",
    )
    assert _pick(regs) == "default"
    assert _pick(regs, explicit="acme_manual") == "acme_manual"
