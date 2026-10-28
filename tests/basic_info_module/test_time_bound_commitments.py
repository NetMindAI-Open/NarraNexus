"""
@file_name: test_time_bound_commitments.py
@date: 2026-08-18
@description: Lock the "a promise with a time on it must be scheduled" rule.

An agent that says "I'll let you know on Friday" has, as far as the runtime
is concerned, said nothing: no part of the system re-reads a reply at the
appointed moment. The commitment expires silently, and from the user's side
that reads as being ignored rather than as a missed reminder.

The rule therefore has to live in the always-loaded module (JobModule is
`module_type="task"` and is exactly the module that may NOT be loaded at the
moment the agent makes a promise), and it has to survive into BOTH prompt
layouts — the stable one and the legacy one.
"""

from narranexus_plugins.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,
    BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    BASIC_INFO_REAL_WORLD_TURN_TEMPLATE,
)


def test_rule_present_in_both_prompt_layouts():
    for template in (BASIC_INFO_MODULE_INSTRUCTIONS,
                     BASIC_INFO_MODULE_INSTRUCTIONS_STABLE):
        assert "Time-bound Commitments" in template
        assert "job_create" in template


def test_rule_names_the_date_tools_rather_than_asking_for_mental_arithmetic():
    assert "resolve_relative_date" in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
    assert "compare_dates" in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE


def test_rule_lives_outside_the_volatile_span():
    """The rule is static, so it belongs in the cacheable prefix.

    `BASIC_INFO_REAL_WORLD_TURN_TEMPLATE` is the per-turn span that gets
    swapped out for a pointer under the relocation flag. Putting static text
    inside it would ship the rule into the turn-context block on every turn —
    paying for it forever and dropping it from the system prompt.
    """
    assert "Time-bound Commitments" not in BASIC_INFO_REAL_WORLD_TURN_TEMPLATE


def test_stable_template_still_derives_from_the_legacy_one():
    """The derivation is a `.replace()` of the exact volatile span; a stray
    edit inside that span silently yields an unchanged (still time-varying)
    stable template."""
    assert BASIC_INFO_REAL_WORLD_TURN_TEMPLATE in BASIC_INFO_MODULE_INSTRUCTIONS
    assert BASIC_INFO_REAL_WORLD_TURN_TEMPLATE not in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
    assert "{current_time}" not in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
