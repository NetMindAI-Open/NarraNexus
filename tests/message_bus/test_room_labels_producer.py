"""
@file_name: test_room_labels_producer.py
@author:
@date: 2026-08-18
@description: `_room_labels` against a real database — the PRODUCER, not the
              renderer.

`test_turn_context_split.py` covers what the tag renders by putting
`bus_room_labels` into `extra_data` by hand. That left the function that builds
the map with no test at all, and it shipped broken: the two queries used `%s`
against `bus._db`, which is the RAW backend, where `%s` is not a placeholder.
Both raised, the fail-open swallowed it, and on SQLite the map was ALWAYS empty —
so every team-room message rendered in the private-conversation form, the one
mislabelling the function's own docstring says must not happen. On MySQL it
worked, so the desktop and the cloud disagreed (铁律 #7) with nothing in the logs
above debug.

6433 passing tests did not touch it. A renderer test that supplies its own input
proves the renderer; it says nothing about where the input comes from.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.message_bus_module.message_bus_module import (
    MessageBusModule,
)
from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

AGENT, TEAM = "agent_me", "t_lbl"
ROOM, DM = "ch_room_lbl", "ch_dm_lbl"


def _module(db_client) -> MessageBusModule:
    return MessageBusModule(AGENT, "usr_1", db_client)


def _patch_db(monkeypatch, db_client):
    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )


async def _seed(db):
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": "usr_1", "name": "Ops"})
    await db.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })
    await db.insert("bus_channels", {
        "channel_id": DM, "name": "dm", "channel_type": "direct",
        "created_by": AGENT,
    })


@pytest.mark.asyncio
async def test_a_team_room_resolves_to_its_team_name(db_client, monkeypatch):
    """The whole point, and what was silently returning {} on SQLite."""
    _patch_db(monkeypatch, db_client)
    await _seed(db_client)

    labels = await _module(db_client)._room_labels({ROOM})

    assert labels == {ROOM: {"name": "Ops", "team_id": TEAM}}, (
        "the room did not resolve — if this is {} the queries are failing and "
        "the fail-open is hiding it"
    )


@pytest.mark.asyncio
async def test_a_private_conversation_gets_no_label(db_client, monkeypatch):
    """Absent, not guessed. A label invented for a DM would tell the agent it is
    in a room, and the reply disciplines for the two differ."""
    _patch_db(monkeypatch, db_client)
    await _seed(db_client)

    assert await _module(db_client)._room_labels({DM}) == {}


@pytest.mark.asyncio
async def test_a_mixed_window_labels_only_the_rooms(db_client, monkeypatch):
    """The real shape: the unread window mixes both kinds, and both queries run
    with a multi-placeholder IN list — the part that was dialect-broken."""
    _patch_db(monkeypatch, db_client)
    await _seed(db_client)

    labels = await _module(db_client)._room_labels({ROOM, DM, "ch_unknown"})

    assert labels == {ROOM: {"name": "Ops", "team_id": TEAM}}


@pytest.mark.asyncio
async def test_a_room_whose_team_row_is_gone_is_absent_not_raw(db_client, monkeypatch):
    """A room pointing at a deleted team must not fall back to printing the id.

    The team id is what the marker holds, so the tempting fallback is to render
    it — which puts `t_lbl` in front of the agent, i.e. the internal handle this
    redesign exists to remove from its vocabulary.
    """
    _patch_db(monkeypatch, db_client)
    await db_client.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": f"{TEAM_ROOM_OWNER_PREFIX}{TEAM}",
    })  # no `teams` row

    assert await _module(db_client)._room_labels({ROOM}) == {}


@pytest.mark.asyncio
async def test_an_empty_window_asks_the_database_nothing(db_client, monkeypatch):
    """Runs on every turn, so the no-work case must not cost a round-trip."""
    calls = []

    class _Counting:
        async def execute(self, *a, **k):
            calls.append(a)
            return []

    async def _async_db():
        return _Counting()

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    assert await _module(db_client)._room_labels(set()) == {}
    assert calls == []
