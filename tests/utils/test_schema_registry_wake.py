"""
@file_name: test_schema_registry_wake.py
@date: 2026-08-17
@description: The wake signal's table — one row, two dialects.

`MessageBusTrigger._wake` is an in-process `asyncio.Event`, so it cannot reach the
MCP server — and a team reply is a tool call made there. Without a signal the poll
loop sees the message on its next tick (3-12s), which is dead air a person in the
room perceives, i.e. exactly what iron rule #16 forbids.

Pinned here rather than left to the code: the table is what makes the mechanism
work on BOTH backends, and a column declared for only one dialect is the shape of
bug the dual-dialect contract exists to prevent — it would pass every local
SQLite test and fail on the cloud, where production runs.
"""
from narranexus.platform.utils.db.schema_registry import get_registered_tables


def _table():
    return {t.name: t for t in get_registered_tables()}["bus_wake"]


def test_the_table_is_registered():
    assert _table() is not None


def test_it_is_one_row_addressed_by_a_fixed_id():
    """A queue would be a second answer to "where is the work", which the poll
    loop already answers. One row, overwritten."""
    cols = {c.name: c for c in _table().columns}
    assert set(cols) == {"id", "bumped_at"}
    assert cols["id"].primary_key


def test_both_dialects_are_declared():
    """The half that only fails in production if it is missing."""
    for col in _table().columns:
        assert col.sqlite_type, f"{col.name} has no SQLite type"
        assert col.mysql_type, f"{col.name} has no MySQL type"


def test_the_stamp_is_not_nullable():
    """A NULL stamp would read as "no news" forever — the signal would be a
    no-op that looks wired."""
    cols = {c.name: c for c in _table().columns}
    assert cols["bumped_at"].nullable is False


def test_the_module_and_the_table_agree_on_the_name():
    """Two literals for one table is how a rename half-lands."""
    from narranexus.platform.message_bus import wake_signal

    assert wake_signal.TABLE == _table().name
