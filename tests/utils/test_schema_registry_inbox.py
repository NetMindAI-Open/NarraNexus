"""
@file_name: test_schema_registry_inbox.py
@author:
@date: 2026-08-17
@description: The inbox's own tables — the record layer, decoupled from the bus.

The inbox used to live in `bus_messages` / `bus_channel_members`. Two costs,
both measured on prod 2026-08-17:

  * 86% of `bus_messages` (28,605 of 33,164 rows) was IM inbox content, not bus
    traffic. The table's name described its minority.
  * The writer created a `bus_channel_members` row per agent, and nothing ever
    advanced its `last_read_at` — 159 of 172 IM memberships (92%) had a NULL
    cursor. That made 1,364 IM messages permanently "unread" on the bus, which
    injected them into 90 agents' turn context as if a peer had written them.

So the inbox gets its own tables and the agent's unread injection cannot reach
them — not by a filter, but because the rows are not in the table it reads.
"""
from narranexus.platform.utils.db.schema_registry import get_registered_tables


def _tables():
    return {t.name: t for t in get_registered_tables()}


def test_both_inbox_tables_are_registered():
    tables = _tables()
    assert "inbox_threads" in tables
    assert "inbox_thread_messages" in tables


def test_a_thread_knows_whose_inbox_it_is_and_what_it_is():
    """The panel lists by owner; the thread has to carry enough to render a row
    without joining back into the bus."""
    cols = {c.name for c in _tables()["inbox_threads"].columns}
    assert {
        "thread_id",
        "owner_user_id",
        "agent_id",
        "source",
        "title",
        "counterpart_id",
        "counterpart_name",
    }.issubset(cols), f"missing: {cols}"


def test_the_thread_carries_its_own_read_state():
    """The UI cursor and the agent's context gate must be different columns in
    different tables.

    They were the same column (`bus_channel_members.last_read_at`), so the
    panel's "mark read" button changed what the agent was handed on its next
    turn. That entanglement already caused one incident: preferring
    `last_processed_at` for the count made it read 0 forever, so the one control
    that could advance the cursor became unreachable exactly in the rooms whose
    backlog was growing.
    """
    cols = {c.name for c in _tables()["inbox_threads"].columns}
    assert "last_read_at" in cols


def test_a_message_records_its_direction_and_sender():
    cols = {c.name for c in _tables()["inbox_thread_messages"].columns}
    assert {
        "message_id",
        "thread_id",
        "direction",
        "sender_id",
        "sender_name",
        "content",
        "attachments",
        "created_at",
    }.issubset(cols), f"missing: {cols}"


def test_backfill_idempotency_is_guaranteed_by_the_database():
    """`source_message_id` + a UNIQUE index — not by the backfill script being
    correct.

    Decision ③ (2026-08-17): history is backfilled, and the Owner runs it BY
    HAND after deploy. So the new write path is already filling these tables
    when the backfill starts, and the overlapping window would double every
    message in it — in a surface the user looks at.

    A unique constraint makes the second insert a no-op no matter how many times
    the script runs, or how wrong its time window is. A script-side "have I seen
    this id" check would put the same guarantee somewhere it can be forgotten.
    """
    table = _tables()["inbox_thread_messages"]
    cols = {c.name for c in table.columns}
    assert "source_message_id" in cols

    unique_on_source = [
        idx for idx in table.indexes
        if idx.columns == ["source_message_id"] and idx.unique
    ]
    assert unique_on_source, (
        "source_message_id needs a UNIQUE index; without it the backfill's "
        "idempotency rests on the script instead of the database"
    )


def test_source_message_id_is_nullable():
    """Only backfilled rows have one. A message written live has no bus row
    behind it, and a NOT NULL column would force the writer to invent an id —
    which would then collide with a real one at backfill time."""
    col = next(
        c for c in _tables()["inbox_thread_messages"].columns
        if c.name == "source_message_id"
    )
    assert col.nullable is not False


def test_threads_are_indexed_the_way_the_panel_queries_them():
    """The panel asks "this user's threads, newest first" on every poll."""
    idx_cols = [idx.columns for idx in _tables()["inbox_threads"].indexes]
    assert ["owner_user_id"] in idx_cols or ["owner_user_id", "last_message_at"] in idx_cols


def test_messages_are_indexed_by_thread():
    idx_cols = [idx.columns for idx in _tables()["inbox_thread_messages"].indexes]
    assert any(cols[:1] == ["thread_id"] for cols in idx_cols), idx_cols


def test_every_column_declares_both_dialects():
    """`auto_migrate()` picks per backend; a missing half only fails on the
    dialect nobody ran locally — and production is the MySQL one."""
    for name in ("inbox_threads", "inbox_thread_messages"):
        for col in _tables()[name].columns:
            assert col.sqlite_type, f"{name}.{col.name} has no sqlite_type"
            assert col.mysql_type, f"{name}.{col.name} has no mysql_type"
