"""
@file_name: test_legacy_credential_purge.py
@author: Bin Liang
@date: 2026-09-07
@description: "Delete my agent" must mean "delete my credentials" — including the retired per-channel tables, including for channels this distribution does not install.

Batch 4d moved every channel credential into the generic ``channel_credentials``
table but kept the six retired tables (rule #6: a migration never drops data).
They still hold base64 bot tokens and app secrets.

Two holes closed here, both of which left exactly those secrets behind forever:

* ``ChannelModuleBase.cleanup_for_agent`` purged them AFTER an early return
  taken when the agent has no GENERIC row — i.e. it skipped precisely the agent
  whose legacy row was never copied. (The copy goes through ``descriptor_for``
  and is skipped for any channel this distribution does not install.)
* The cleanup walk only visits registered ``ChannelModuleBase`` subclasses, so a
  channel excluded from the distribution — both shipped example distributions
  exclude all six — got no cleanup at all. ``purge_legacy_for_agent`` sweeps
  ``LEGACY_TABLES`` directly, keyed on ``agent_id``, and the agent-deletion route
  calls it once for every table regardless of what is installed.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.credential_legacy import LEGACY_TABLES, purge_legacy_for_agent

# The retired tables have NOT NULL secret columns (they were written by the
# pre-4d managers); a row has to look like a real one to insert at all.
_REQUIRED = {
    # profile_name is UNIQUE, so it is derived from the agent id below.
    "lark_credentials": {"app_id": "cli_x", "app_secret_ref": "ref", "brand": "lark"},
    "channel_slack_credentials": {"bot_token_encoded": "eA==", "app_token_encoded": "eA=="},
    "channel_telegram_credentials": {"bot_token_encoded": "eA=="},
    "channel_wechat_credentials": {"bot_token_encoded": "eA=="},
    "channel_narramessenger_credentials": {"bearer_token_encoded": "eA=="},
    "channel_discord_credentials": {"bot_token_encoded": "eA=="},
}


async def _insert_legacy(db, table: str, agent_id: str) -> None:
    row = {"agent_id": agent_id, **_REQUIRED[table]}
    if table == "lark_credentials":
        row["profile_name"] = f"profile_{agent_id}"
    await db.insert(table, row)


def _channel_module(channel: str):
    """A minimal concrete ``ChannelModuleBase``: the cleanup path under test uses
    only ``channel_name`` and the db, so the abstract surface is stubbed rather
    than dragging a real channel plugin (and its SDK) into this test."""
    from narranexus.platform.channel.channel_module_base import ChannelModuleBase

    class _Stub(ChannelModuleBase):
        channel_name = channel

        def __init__(self):  # the real __init__ wants a full module context
            pass

        def get_config(self): ...
        async def get_credential(self, *a, **k): ...
        async def build_extra_data(self, *a, **k): ...
        def contribute_instructions(self, *a, **k): ...
        def register_mcp_tools(self, *a, **k): ...
        async def send_to_agent(self, *a, **k): ...

    return _Stub()


@pytest.mark.asyncio
async def test_purge_removes_rows_for_every_legacy_table(db_client):
    """The distribution-independent sweep: no descriptor, no plugin, no generic row."""
    for spec in LEGACY_TABLES:
        await _insert_legacy(db_client, spec.table, "agt_doomed")
        await _insert_legacy(db_client, spec.table, "agt_survivor")

    purged = await purge_legacy_for_agent(db_client, "agt_doomed")

    assert set(purged) == {spec.table for spec in LEGACY_TABLES}
    for spec in LEGACY_TABLES:
        assert await db_client.get(spec.table, {"agent_id": "agt_doomed"}) == []
        assert len(await db_client.get(spec.table, {"agent_id": "agt_survivor"})) == 1


@pytest.mark.asyncio
async def test_purge_narrowed_to_one_channel_leaves_the_others(db_client):
    """What ``ChannelModuleBase`` passes: its own channel only, so the per-channel
    walk stays per-channel and cannot double-count into another channel's stats."""
    for spec in LEGACY_TABLES:
        await _insert_legacy(db_client, spec.table, "agt_1")

    purged = await purge_legacy_for_agent(db_client, "agt_1", channel="lark")

    assert set(purged) == {"lark_credentials"}
    for spec in LEGACY_TABLES:
        rows = await db_client.get(spec.table, {"agent_id": "agt_1"})
        assert rows == [] if spec.channel == "lark" else len(rows) == 1


@pytest.mark.asyncio
async def test_purge_is_best_effort_when_a_table_is_absent(db_client):
    """An install that never had the table must not fail the deletion."""

    class _MissingTable:
        async def delete(self, table, filters):
            raise RuntimeError(f"no such table: {table}")

    assert await purge_legacy_for_agent(_MissingTable(), "agt_1") == {}


@pytest.mark.asyncio
async def test_cleanup_purges_legacy_rows_for_an_agent_with_no_generic_row(db_client):
    """The regression this exists for.

    The agent has a ``lark_credentials`` row and NO ``channel_credentials`` row —
    the exact state the early return used to skip, and the state every agent of an
    uninstalled channel is in."""
    await _insert_legacy(db_client, "lark_credentials", "agt_orphan")

    stats = await _channel_module("lark").cleanup_for_agent("agt_orphan", db_client)

    assert await db_client.get("lark_credentials", {"agent_id": "agt_orphan"}) == []
    # No generic row existed, so nothing is claimed in stats — one entry per
    # channel, and only when something was actually unbound.
    assert stats == {}


@pytest.mark.asyncio
async def test_cleanup_does_not_touch_another_channels_legacy_rows(db_client):
    """An excluded channel's rows are the AGENT-DELETION route's job (the sweep
    above), not another channel module's — the per-channel purge stays narrow."""
    await _insert_legacy(db_client, "lark_credentials", "agt_2")
    await _insert_legacy(db_client, "channel_slack_credentials", "agt_2")

    await _channel_module("lark").cleanup_for_agent("agt_2", db_client)

    assert await db_client.get("lark_credentials", {"agent_id": "agt_2"}) == []
    assert len(await db_client.get("channel_slack_credentials", {"agent_id": "agt_2"})) == 1
