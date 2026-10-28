"""
@file_name: test_onboarding_profile.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for the onboarding guide-agent bootstrap profile and
              its persona / topic pools (registration, bilingual greeting,
              local-install provider notice, ctx.extra plumbing).
"""

import random

import backend.onboarding.profile  # noqa: F401 — registers the "onboarding" profile
from backend.onboarding.personas import (
    PERSONAS,
    TOPIC_OPENERS,
    persona_by_key,
    pick_persona,
    pick_topic_index,
    render_awareness,
    render_greeting,
)
from narranexus.platform.bootstrap.profiles import BootstrapContext, get_profile


def test_onboarding_profile_registered():
    assert get_profile("onboarding").name == "onboarding"
    assert get_profile("onboarding").auto_delete_after_events == 3


def test_pools_are_nonempty_and_bilingual():
    assert len(PERSONAS) >= 4 and len(TOPIC_OPENERS) >= 4
    for p in PERSONAS:
        assert p["tagline_en"] and p["tagline_zh"] and p["awareness"]
    for t in TOPIC_OPENERS:
        assert t["en"] and t["zh"]


def test_pick_helpers_use_rng():
    rng = random.Random(5)
    assert pick_persona(rng) in PERSONAS
    assert 0 <= pick_topic_index(random.Random(5)) < len(TOPIC_OPENERS)


def test_persona_by_key_falls_back():
    assert persona_by_key("no-such-key") == PERSONAS[0]
    assert persona_by_key(PERSONAS[2]["key"]) == PERSONAS[2]


def test_greeting_is_bilingual_with_topic_and_cancel_hint():
    p, t = PERSONAS[0], TOPIC_OPENERS[0]
    g = render_greeting("Brave_Nova_Fox", p, t, is_local=False)
    assert "Brave_Nova_Fox" in g
    assert t["en"] in g and t["zh"] in g
    assert "Daily check-in" in g  # how to switch off the proactive job
    assert "provider" not in g.lower()  # cloud greeting has no local notice


def test_greeting_local_mode_adds_provider_notice():
    g = render_greeting("X", PERSONAS[0], TOPIC_OPENERS[0], is_local=True)
    assert "provider" in g.lower()
    assert "本地版" in g


def test_awareness_has_discipline_skill_pointer_and_persona():
    a = render_awareness("Guide_X", PERSONAS[1], is_local=False)
    assert "narranexus-guide" in a
    assert "pause" in a.lower()
    assert PERSONAS[1]["awareness"] in a
    assert "LOCAL INSTALL" not in a


def test_awareness_local_notice():
    a = render_awareness("Guide_X", PERSONAS[1], is_local=True)
    assert "LOCAL INSTALL" in a


def test_profile_renders_via_ctx_extra():
    p = get_profile("onboarding")
    ctx = BootstrapContext(
        agent_id="agent_a",
        user_id="u",
        agent_name="Witty_Solar_Lynx",
        extra={"persona_key": PERSONAS[1]["key"], "topic_index": 3, "is_local": True},
    )
    g = p.greeting(ctx)
    assert "Witty_Solar_Lynx" in g
    assert TOPIC_OPENERS[3]["en"] in g
    assert "provider" in g.lower()
    md = p.bootstrap_md(ctx)
    assert md is not None and md.startswith("# Bootstrap")
    assert p.welcome_artifact(ctx) is not None


def test_profile_defaults_on_bare_ctx():
    p = get_profile("onboarding")
    g = p.greeting(BootstrapContext(agent_id="a", user_id="u", agent_name="N"))
    assert "N" in g and TOPIC_OPENERS[0]["en"] in g
