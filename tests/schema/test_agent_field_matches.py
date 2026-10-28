"""
@file_name: test_agent_field_matches.py
@author: NarraNexus
@date: 2026-08-17
@description: Direct tests for the shared "would this write change anything"
predicate — the one place where an error cannot be caught downstream.

`update_agent` uses `agent_field_matches` twice: to decide which fields to
write, and afterwards to decide whether the write landed. That sharing is what
keeps the two answers consistent, but it also means the re-read verification is
STRUCTURALLY BLIND to a fault in the predicate itself: a wrong "already equal"
suppresses the write AND then certifies the row as correct, returning
success=True with no error logged anywhere. Nothing downstream can catch it, so
it is pinned here.

The `is_public` branch is the fragile one: the column is TINYINT on MySQL and
INTEGER on SQLite, and `_row_to_entity` may hand back a bool or an int. A
misjudgement there makes the visibility toggle "succeed" while doing nothing —
quieter than the rowcount bug this all started with, which at least shouted.
"""
from __future__ import annotations

import pytest

from narranexus.platform.schema import Agent, agent_field_matches, normalize_agent_text


def _agent(**kwargs) -> Agent:
    base = dict(agent_id="a1", agent_name="小绿", created_by="alice")
    base.update(kwargs)
    return Agent(**base)


class TestIsPublic:
    """Every shape the column and the caller can take."""

    @pytest.mark.parametrize("stored", [True, False])
    @pytest.mark.parametrize("wanted", [True, False, 1, 0])
    def test_agrees_across_bool_and_int_forms(self, stored, wanted):
        agent = _agent(is_public=stored)
        assert agent_field_matches(agent, "is_public", wanted) is (
            bool(stored) == bool(wanted)
        )

    def test_a_real_toggle_is_never_reported_as_already_equal(self):
        """The silent-no-op case: this returning True would skip the write and
        then certify the unchanged row as correct."""
        assert agent_field_matches(_agent(is_public=False), "is_public", 1) is False
        assert agent_field_matches(_agent(is_public=True), "is_public", 0) is False


class TestText:
    def test_null_and_empty_are_the_same_absence_of_text(self):
        assert agent_field_matches(_agent(agent_description=None), "agent_description", "")
        assert agent_field_matches(_agent(agent_description=""), "agent_description", "")

    def test_clearing_a_description_that_has_text_is_a_change(self):
        assert not agent_field_matches(
            _agent(agent_description="精通各地美食推荐"), "agent_description", ""
        )

    def test_surrounding_whitespace_is_not_content(self):
        """Both writers now agree on this; they used to disagree, which is why
        the same input was 'unchanged' on one path and a write on the other."""
        assert agent_field_matches(_agent(agent_name="小绿"), "agent_name", "  小绿  ")
        assert agent_field_matches(_agent(agent_name=" 小绿 "), "agent_name", "小绿")

    def test_a_different_name_is_a_change(self):
        assert not agent_field_matches(_agent(agent_name="美食家"), "agent_name", "小绿")


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [(None, ""), ("", ""), ("   ", ""), (" x ", "x"), ("小绿", "小绿")],
    )
    def test_stored_form(self, raw, expected):
        assert normalize_agent_text(raw) == expected


def test_an_unknown_field_is_refused_rather_than_silently_equal():
    """A typo'd field name must not read as 'nothing to do' — that is exactly
    the failure mode the predicate cannot otherwise surface."""
    with pytest.raises(ValueError):
        agent_field_matches(_agent(), "agent_metadata", "x")


@pytest.mark.parametrize("wanted", [None, 3, b"x", ["x"]])
def test_a_non_str_for_a_text_field_is_refused_too(wanted):
    """Same failure mode one level down: coerced to "" it would answer
    'already equal' for an empty row, suppressing the write and certifying it.
    """
    with pytest.raises(TypeError):
        agent_field_matches(_agent(agent_description=""), "agent_description", wanted)


def test_is_public_still_takes_bool_and_int():
    """The type guard is on the TEXT branch only — narrowing the signature
    would break this one."""
    assert agent_field_matches(_agent(is_public=True), "is_public", True)
    assert agent_field_matches(_agent(is_public=True), "is_public", 1)


class TestCreatedBy:
    """Owner id — an exact match, never normalized.

    Added when the Manyfold provisioning rerun started routing through the
    shared profile transaction: it rewrites ``created_by`` alongside the name,
    and the predicate refuses any field it has no comparison for.
    """

    def test_the_same_owner_needs_no_write(self):
        assert agent_field_matches(_agent(created_by="alice"), "created_by", "alice")

    def test_a_different_owner_needs_a_write(self):
        assert not agent_field_matches(_agent(created_by="alice"), "created_by", "bob")

    @pytest.mark.parametrize("wanted", [" alice", "alice ", "ALICE"])
    def test_an_id_is_not_normalized_the_way_display_text_is(self, wanted):
        """Stripping or case-folding an identifier used as a lookup key would
        suppress the write and leave the row owned by an id nothing resolves.
        The text fields normalize; this one must not."""
        assert not agent_field_matches(_agent(created_by="alice"), "created_by", wanted)

    def test_none_and_empty_are_the_same_absence(self):
        assert agent_field_matches(_agent(created_by=""), "created_by", None)

    @pytest.mark.parametrize("wanted", [3, b"x", ["x"]])
    def test_a_non_str_owner_is_refused_like_every_other_field(self, wanted):
        """This branch returns before the text branch's type guard, so it needs
        its own — otherwise it is the one documented hole in a predicate whose
        whole design is that wrong-typed input fails loudly."""
        with pytest.raises(TypeError):
            agent_field_matches(_agent(created_by="alice"), "created_by", wanted)
