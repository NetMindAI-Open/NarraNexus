"""
@file_name: test_chat_writes.py
@author: Bin Liang
@date: 2026-08-20
@description: Lock the single bootstrap-greeting writer (chat_module/_chat_writes).

Exercises the REAL EventMemoryRepository (the db_client fixture) so the UPSERT
path and the timestamp serialization are actually executed — the earlier
fully-mocked tests hid exactly these. Covers: idempotency (no duplicate
greeting), timestamp anchored to turn-start - 1ms (orders before the user
message), and aware-UTC serialization even from a naive datetime (the
MySQL-naive P0).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus_plugins.chat_module._chat_writes import (
    agent_chat_has_history,
    build_bootstrap_greeting_row,
    seed_bootstrap_greeting,
)
from narranexus.platform.repository.event_memory_repository import EventMemoryRepository
from narranexus.platform.utils import utc_now

_INSTANCE = "chat_writes_instance"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


# ---- build_bootstrap_greeting_row (pure) --------------------------------


def test_row_timestamp_precedes_turn_start():
    turn_start = utc_now()
    row = build_bootstrap_greeting_row("hello", turn_start, _INSTANCE)
    assert row["role"] == "assistant"
    assert row["content"] == "hello"
    assert row["meta_data"]["bootstrap"] is True
    assert _parse(row["meta_data"]["timestamp"]) < turn_start


def test_row_naive_datetime_serializes_as_aware_utc():
    """A naive turn_started_at (MySQL returns naive) must still emit a tz-aware
    string so the browser's new Date() reads it as UTC, not local — the P0."""
    naive = datetime(2026, 8, 20, 4, 10, 0)  # no tzinfo
    row = build_bootstrap_greeting_row("hi", naive, _INSTANCE)
    parsed = _parse(row["meta_data"]["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_row_event_id_optional():
    row = build_bootstrap_greeting_row("hi", utc_now(), _INSTANCE)
    assert "event_id" not in row["meta_data"]
    row2 = build_bootstrap_greeting_row("hi", utc_now(), _INSTANCE, event_id="evt_1")
    assert row2["meta_data"]["event_id"] == "evt_1"


# ---- seed_bootstrap_greeting (real repository) --------------------------


@pytest.mark.asyncio
async def test_seed_writes_into_empty_instance(db_client):
    turn_start = utc_now()
    wrote = await seed_bootstrap_greeting(
        db_client, "a_w", "u_w", _INSTANCE, "Greetings!", turn_start
    )
    assert wrote is True

    repo = EventMemoryRepository("a_w", "u_w", db_client)
    mem = await repo.search_instance_json_format_memory("ChatModule", _INSTANCE)
    msgs = mem.get("messages", [])
    assert len(msgs) == 1
    assert msgs[0]["content"] == "Greetings!"
    assert msgs[0]["meta_data"]["bootstrap"] is True
    assert _parse(msgs[0]["meta_data"]["timestamp"]) < turn_start


@pytest.mark.asyncio
async def test_seed_is_idempotent_on_nonempty_instance(db_client):
    """A second seed (or a seed racing the hook) must NOT append a duplicate."""
    inst = "chat_writes_idem"
    repo = EventMemoryRepository("a_i", "u_i", db_client)
    # Pre-existing history (as if the hook already wrote, or a prior turn).
    await repo.add_instance_json_format_memory(
        "ChatModule",
        inst,
        {"messages": [{"role": "user", "content": "hey", "meta_data": {"timestamp": utc_now().isoformat()}}]},
    )

    wrote = await seed_bootstrap_greeting(
        db_client, "a_i", "u_i", inst, "Greetings!", utc_now()
    )

    assert wrote is False
    mem = await repo.search_instance_json_format_memory("ChatModule", inst)
    msgs = mem.get("messages", [])
    assert len(msgs) == 1  # unchanged
    assert all(m["meta_data"].get("bootstrap") is not True for m in msgs)


# ---- per-(agent,user) scope: the Shenzhen-r2 "two replies" bug ----------


async def _register_chat_instance(db_client, instance_id: str, agent: str, user: str):
    await db_client.insert(
        "module_instances",
        {
            "instance_id": instance_id,
            "module_class": "ChatModule",
            "agent_id": agent,
            "user_id": user,
            "status": "active",
        },
    )


@pytest.mark.asyncio
async def test_seed_skips_when_a_sibling_instance_already_has_history(db_client):
    """Shenzhen-r2 B2: every new narrative creates a fresh EMPTY chat
    instance, and the per-instance guard alone re-seeded the greeting there —
    the extra assistant row popped in near the user's question and read as a
    second reply. The greeting is a first-contact affordance: once the agent
    has ANY chat history with this user, no instance is ever seeded again."""
    agent, user = "a_sib", "u_sib"
    await _register_chat_instance(db_client, "chat_sib_a", agent, user)
    await _register_chat_instance(db_client, "chat_sib_b", agent, user)
    repo = EventMemoryRepository(agent, user, db_client)
    await repo.add_instance_json_format_memory(
        "ChatModule",
        "chat_sib_a",
        {"messages": [
            {"role": "user", "content": "name yourself", "meta_data": {"timestamp": utc_now().isoformat()}},
            {"role": "assistant", "content": "done", "meta_data": {"timestamp": utc_now().isoformat()}},
        ]},
    )

    wrote = await seed_bootstrap_greeting(
        db_client, agent, user, "chat_sib_b", "Greetings!", utc_now()
    )

    assert wrote is False
    mem = await repo.search_instance_json_format_memory("ChatModule", "chat_sib_b")
    assert not (mem or {}).get("messages")


@pytest.mark.asyncio
async def test_seed_still_fires_on_true_first_contact_with_registered_instances(db_client):
    """The sibling scan must not false-positive on registered-but-empty
    instances — a genuinely fresh agent still gets its greeting."""
    agent, user = "a_fresh", "u_fresh"
    await _register_chat_instance(db_client, "chat_fresh_a", agent, user)
    wrote = await seed_bootstrap_greeting(
        db_client, agent, user, "chat_fresh_a", "Greetings!", utc_now()
    )
    assert wrote is True


@pytest.mark.asyncio
async def test_agent_chat_has_history_scopes_by_agent_and_user(db_client):
    """Another agent's (or another user's) history must not suppress this
    agent's first greeting."""
    await _register_chat_instance(db_client, "chat_other_a", "a_other", "u_x")
    repo = EventMemoryRepository("a_other", "u_x", db_client)
    await repo.add_instance_json_format_memory(
        "ChatModule",
        "chat_other_a",
        {"messages": [{"role": "user", "content": "hi", "meta_data": {"timestamp": utc_now().isoformat()}}]},
    )
    assert await agent_chat_has_history(db_client, "a_other", "u_x") is True
    assert await agent_chat_has_history(db_client, "a_new", "u_x") is False
    assert await agent_chat_has_history(db_client, "a_other", "u_y") is False
