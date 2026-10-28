"""
@file_name: test_schema_registry_team_bulletin.py
@author: NarraNexus
@date: 2026-08-10
@description: Schema guards for the team bulletin.

The bulletin fills the gap the PRD names: team-level state that is persistent
AND shared AND loaded every turn. The data layer had the first two available
separately (`teams.description`/`intro_md` are persistent and shared but never
reach a prompt) and the third only in per-agent memory, which is not shared.

Additive only (iron rule #6): one new table, so `auto_migrate` provisions it
without touching any existing row.

The column shape carries two decisions worth guarding, both of which are cheap
to check here and expensive to discover later:

  * `source` and `author_id` are SEPARATE — one drives permissions and budget,
    the other drives display. Merged, a permission check would parse a prefix.
  * `author_id` is NULLABLE — the auto-summary has a source but no author.
    A NOT NULL here would force a sentinel like "system", which then has to be
    excluded by string comparison everywhere a real author is expected.
"""

import pytest

from narranexus.platform.utils.db.schema_registry import TABLES

TABLE = "team_bulletin_entries"


def _cols() -> dict:
    return {c.name: c for c in TABLES[TABLE].columns}


def _index_columns() -> list[list[str]]:
    return [list(i.columns) for i in TABLES[TABLE].indexes]


def test_the_bulletin_table_is_registered():
    assert TABLE in TABLES


@pytest.mark.parametrize("column", ["entry_id", "team_id", "content", "source", "author_id", "tier"])
def test_every_column_declares_both_dialects(column):
    """sqlite_type and mysql_type must BOTH be filled — auto_migrate picks per
    backend, and a missing one is a table that only exists on one of them."""
    col = _cols().get(column)
    assert col is not None, f"{TABLE}.{column} missing"
    assert col.sqlite_type, f"{column}: sqlite_type missing"
    assert col.mysql_type, f"{column}: mysql_type missing"


def test_source_and_author_are_separate_columns():
    """`source` decides the rules (who may delete it, whether it spends the
    budget), `author_id` decides the display. One column would make a
    permission check parse a string prefix."""
    cols = _cols()
    assert "source" in cols and "author_id" in cols


def test_author_is_nullable_because_the_summary_has_none():
    """The auto-summary is written by nobody. NOT NULL would force a sentinel
    author that every "who wrote this" path then has to special-case."""
    assert _cols()["author_id"].nullable is not False


def test_entry_id_is_unique():
    """It is the handle the REST layer and the tool both delete by."""
    col = _cols()["entry_id"]
    assert col.unique or ["entry_id"] in _index_columns()


def test_entries_are_indexed_by_team():
    """Every team turn reads this table. Without the index that is a full scan
    on the hottest path the feature has."""
    assert any("team_id" in cols for cols in _index_columns())


def test_the_summary_slot_is_reachable_by_an_indexed_lookup():
    """`get_summary` filters (team_id, source) on every summary write and on
    every prompt build."""
    assert ["team_id", "source"] in _index_columns()


def test_the_summary_watermark_is_its_own_column():
    """How much has happened since the last summary is tracked in a dedicated
    nullable column, not folded into `author_id`.

    `author_id` already answers "who wrote this". A second meaning that depends
    on `source` is exactly what makes a schema unreadable later — and the first
    draft of the worker did precisely that before this was pinned.
    """
    col = _cols().get("watermark_at")
    assert col is not None, "team_bulletin_entries.watermark_at missing"
    assert col.sqlite_type and col.mysql_type
    assert col.nullable is not False, "only the summary row carries a watermark"
