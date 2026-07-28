"""
@file_name: test_bootstrap_profiles.py
@author: Bin Liang
@date: 2026-06-16
@description: Unit tests for the bootstrap profile abstraction — registry,
              per-profile rendering, the per-profile deletion threshold (B), and
              backward compatibility for pre-profile agents.
"""

# Importing the arena service registers the "arena" profile as a side effect.
import backend.integrations.arena.arena_provisioning_service  # noqa: F401
from xyz_agent_context.bootstrap.profiles import (
    BootstrapContext,
    DEFAULT_AUTO_DELETE_AFTER_EVENTS,
    auto_delete_threshold_from_meta,
    get_profile,
)


def _ctx(**extra):
    return BootstrapContext(agent_id="agent_x", user_id="u", agent_name="Name", extra=extra)


def test_registry_has_builtins_and_arena():
    assert get_profile("default").name == "default"
    assert get_profile("none").name == "none"
    assert get_profile("arena").name == "arena"
    # unknown / None fall back to default
    assert get_profile("does-not-exist").name == "default"
    assert get_profile(None).name == "default"


def test_default_profile_renders_generic_first_run():
    p = get_profile("default")
    assert "woke up" in p.greeting(_ctx())
    assert p.bootstrap_md(_ctx()).startswith("# Bootstrap")
    # B: configurable threshold, default value is 3
    assert p.auto_delete_after_events == DEFAULT_AUTO_DELETE_AFTER_EVENTS == 3


def test_arena_profile_is_gamertag_aware_and_threshold_3():
    p = get_profile("arena")
    g = p.greeting(_ctx(gamertag="Swift_Nova_Wolf"))
    md = p.bootstrap_md(_ctx(gamertag="Swift_Nova_Wolf"))
    assert "Swift_Nova_Wolf" in g and "Swift_Nova_Wolf" in md
    # B confirmed: both built-in types currently use 3
    assert p.auto_delete_after_events == 3


def test_none_profile_opts_out():
    p = get_profile("none")
    assert p.bootstrap_md(_ctx()) is None
    assert p.greeting(_ctx()) == ""
    assert p.auto_delete_after_events is None
    assert p.should_auto_delete(99) is False  # never rule-deletes


def test_should_auto_delete_threshold_logic():
    p = get_profile("default")  # threshold 3
    assert p.should_auto_delete(0) is False
    assert p.should_auto_delete(2) is False
    assert p.should_auto_delete(3) is True
    assert p.should_auto_delete(10) is True


def test_threshold_from_meta_backward_compatible():
    # pre-profile agent (no key) → historical default 3
    assert auto_delete_threshold_from_meta(None) == 3
    assert auto_delete_threshold_from_meta({}) == 3
    assert auto_delete_threshold_from_meta({"other": 1}) == 3
    # explicit values honored, including None (semantic-only)
    assert auto_delete_threshold_from_meta({"bootstrap_auto_delete_after_events": 5}) == 5
    assert auto_delete_threshold_from_meta({"bootstrap_auto_delete_after_events": None}) is None
