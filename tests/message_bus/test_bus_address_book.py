"""
@file_name: test_bus_address_book.py
@author:
@date: 2026-08-20
@description: The bus context carries a STANDING address book — the teams the
agent belongs to, with their ids — so `message_team(team_id=...)` has a target
on every turn, not only when a team room happens to sit in the unread window.

Capability follows the agent, not the trigger channel: an agent woken in a DM
must still be told the id of the team room it wants to post into. `Known Agents`
already carries peer ids; this pins that teams are carried the same way.

Covers all three layers, because a renderer test that supplies its own
`bus_teams` proves the renderer and says NOTHING about the fetch — and it is the
fetch that decides whether a DM turn can reach a room at all (the same trap
`test_room_labels_producer.py` was written to close):
  * `_team_address_book` — the PRODUCER, against a real database;
  * `gather` — the WIRING, membership → producer → extra_data;
  * `_volatile_context_parts` — the RENDERER.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from narranexus_plugins.message_bus_module import message_bus_module as mbm
from narranexus_plugins.message_bus_module.message_bus_module import (
    MAX_TEAMS_IN_CONTEXT,
    MessageBusModule,
)
from narranexus.platform.schema import ContextData

AGENT, OWNER = "agent_a", "usr_1"


def _module(db_client=None) -> MessageBusModule:
    return MessageBusModule(
        agent_id=AGENT, user_id=OWNER, database_client=db_client or MagicMock()
    )


def _ctx(**extra) -> ContextData:
    ctx = ContextData(agent_id=AGENT, user_id=OWNER, input_content="hi")
    ctx.extra_data.update(extra)
    return ctx


# ── the producer: _team_address_book (against a real database) ──────────────


@pytest.mark.asyncio
async def test_producer_reads_names_for_the_teams_it_is_given(db_client):
    """The load-bearing half: id in → {id, name} out, read from the `teams`
    table. Delete the producer body and this goes red — the renderer tests,
    which inject `bus_teams` by hand, would not."""
    await db_client.insert(
        "teams", {"team_id": "team_abc", "owner_user_id": OWNER, "name": "Arena"}
    )
    await db_client.insert(
        "teams", {"team_id": "team_def", "owner_user_id": OWNER, "name": "Briefing"}
    )

    book = await _module(db_client)._team_address_book(
        db_client, ["team_abc", "team_def"]
    )

    assert {"team_id": "team_abc", "name": "Arena"} in book
    assert {"team_id": "team_def", "name": "Briefing"} in book


@pytest.mark.asyncio
async def test_producer_returns_empty_for_no_teams_without_touching_the_db(db_client):
    """Runs on every turn — the no-team case must not cost a round-trip."""
    calls = []

    class _Counting:
        async def get_by_ids(self, *a, **k):
            calls.append(a)
            return []

    assert await _module(db_client)._team_address_book(_Counting(), []) == []
    assert calls == []


@pytest.mark.asyncio
async def test_producer_keeps_a_nameless_team_but_gives_it_the_fallback_label(
    db_client,
):
    """A team is reachable by its id even with no name — the id is the address.
    It must not be dropped for lacking a label; it gets the "Team" fallback."""
    await db_client.insert(
        "teams", {"team_id": "team_ghi", "owner_user_id": OWNER, "name": ""}
    )

    book = await _module(db_client)._team_address_book(db_client, ["team_ghi"])

    assert book == [{"team_id": "team_ghi", "name": "Team"}]


@pytest.mark.asyncio
async def test_producer_caps_the_fetch_not_only_the_render(db_client):
    """The cap is on the fetch, so a pathological owner's team count never rides
    into extra_data in full. Seed one over the cap and confirm the producer
    asked for no more than the cap."""
    asked = []

    class _Spy:
        async def get_by_ids(self, table, field, ids):
            asked.append(ids)
            return []

    ids = [f"team_{i}" for i in range(MAX_TEAMS_IN_CONTEXT + 5)]
    await _module(db_client)._team_address_book(_Spy(), ids)

    assert len(asked[0]) == MAX_TEAMS_IN_CONTEXT


@pytest.mark.asyncio
async def test_producer_swallows_a_db_failure_and_returns_empty(db_client):
    """An address book is never worth a turn: a DB error yields `[]`, not a
    raised turn. Without this the fail-open is a claim no test backs."""

    class _Boom:
        async def get_by_ids(self, *a, **k):
            raise RuntimeError("db down")

    assert await _module(db_client)._team_address_book(_Boom(), ["team_a"]) == []


# ── the wiring: gather (membership → producer → extra_data) ────


def _stub_bus():
    class _Bus:
        async def get_unread(self, *a, **k):
            return []

        async def count_unread(self, *a, **k):
            return 0

    return _Bus()


def _patch_runtime(monkeypatch, db_client):
    async def _bus():
        return _stub_bus()

    async def _db():
        return db_client

    async def _noop_sync(*a, **k):
        return None

    monkeypatch.setattr(mbm, "_get_default_bus_async", _bus)
    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _db
    )
    monkeypatch.setattr(
        "narranexus.platform.message_bus.agent_discovery_sync.sync_agent_discovery",
        _noop_sync,
    )


@pytest.mark.asyncio
async def test_hook_fills_bus_teams_for_a_team_member(db_client, monkeypatch):
    """The whole chain: an agent in a team gets that team — with its id — in
    `bus_teams`, regardless of what channel woke the turn. Delete the hook's
    call to the producer and this goes red."""
    _patch_runtime(monkeypatch, db_client)
    await db_client.insert(
        "agents",
        {"agent_id": AGENT, "created_by": OWNER, "agent_name": "A", "is_public": 0},
    )
    await db_client.insert(
        "teams", {"team_id": "team_x", "owner_user_id": OWNER, "name": "Ops"}
    )
    await db_client.insert("team_members", {"team_id": "team_x", "agent_id": AGENT})

    ctx = await _module(db_client).gather(_ctx())

    assert ctx.extra_data.get("bus_teams") == [{"team_id": "team_x", "name": "Ops"}]


@pytest.mark.asyncio
async def test_hook_leaves_bus_teams_absent_for_a_teamless_agent(
    db_client, monkeypatch
):
    """No team → no key. Absence is silence, not an empty list that reads as
    'you have no teams' next to a room the agent might actually be in."""
    _patch_runtime(monkeypatch, db_client)
    await db_client.insert(
        "agents",
        {"agent_id": AGENT, "created_by": OWNER, "agent_name": "A", "is_public": 0},
    )

    ctx = await _module(db_client).gather(_ctx())

    assert "bus_teams" not in ctx.extra_data


# ── the renderer: _volatile_context_parts ──────────────────────────────────


def test_teams_are_rendered_with_their_ids():
    """The list must print `team_id` (the argument `message_team` needs), not
    just the name — a name the agent cannot pass as `team_id` is not an
    address."""
    ctx = _ctx(
        bus_teams=[
            {"team_id": "team_abc123", "name": "Arena"},
            {"team_id": "team_def456", "name": "Briefing Squad"},
        ]
    )

    text = "\n".join(_module()._volatile_context_parts(ctx))

    assert "team_abc123" in text
    assert "Arena" in text
    assert "team_def456" in text
    arena_line = next(ln for ln in text.splitlines() if "Arena" in ln)
    assert "team_abc123" in arena_line


def test_no_teams_renders_no_teams_section():
    """An agent in no team gets no empty heading."""
    text = "\n".join(_module()._volatile_context_parts(_ctx()))

    assert "Your teams" not in text


def test_renderer_caps_the_list_and_the_heading_agrees_with_it():
    """The render-side cap is a second copy of the same knowledge as the fetch
    cap; pin it so the two cannot drift. The `(top N)` heading must equal the
    number of lines actually printed, or it lies about what was shown."""
    over = [
        {"team_id": f"team_{i}", "name": f"T{i}"}
        for i in range(MAX_TEAMS_IN_CONTEXT + 3)
    ]

    lines = _module()._volatile_context_parts(_ctx(bus_teams=over))
    team_lines = [ln for ln in lines if ln.startswith("- `team_")]
    heading = next(ln for ln in lines if ln.startswith("### Your teams"))

    assert len(team_lines) == MAX_TEAMS_IN_CONTEXT
    assert f"(top {MAX_TEAMS_IN_CONTEXT})" in heading
