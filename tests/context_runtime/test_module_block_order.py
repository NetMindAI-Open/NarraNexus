"""
@file_name: test_module_block_order.py
@author: NarraNexus
@date: 2026-07-28
@description: R4d — module block ordering must be a TOTAL order.

`sorted(..., key=lambda x: x.priority)` is stable, so PRIORITY TIES inherited
whatever order came out of InstanceRepository.get_public_instances(), which
had no `order_by` at all. Live ties exist today: BasicInfo(2)/GeneralMemory(2),
Awareness(3)/SocialNetwork(3), Lark(6)/Discord(6)/Slack(6),
Telegram(7)/WeChat(7). An Awareness<->SocialNetwork swap moves ~4018 and
~4880 bytes with ZERO net length change — a same-length reorder that
punctures the cacheable system-prompt prefix while every byte-count
diagnostic reports "no change". SQLite happens to return rowid order today;
Postgres/MySQL promise nothing, so this is latent, not theoretical.

Both layers are locked here: the prompt-side total sort, and the repository
query actually issuing an ORDER BY.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.repository.instance_repository import InstanceRepository
from xyz_agent_context.schema.module_schema import ModuleInstructions


def _runtime() -> ContextRuntime:
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = "agent_block_order"
    runtime.user_id = None  # __init__ skipped; identity seam reads it
    return runtime


# The live priority map, ties included (see module get_config()s).
_LIVE_MODULES = [
    ("AwarenessModule", 0),
    ("ChatModule", 1),
    ("BasicInfoModule", 2),
    ("GeneralMemoryModule", 2),
    ("AwarenessTasksModule", 3),
    ("SocialNetworkModule", 3),
    ("LarkModule", 6),
    ("DiscordModule", 6),
    ("SlackModule", 6),
    ("TelegramModule", 7),
    ("WeChatModule", 7),
]


def _instructions() -> list[ModuleInstructions]:
    # Deliberately different sizes so a swap of two tied-priority modules is
    # a pure reorder with zero net length change ONLY in aggregate.
    return [
        ModuleInstructions(name=name, instruction=f"[{name}]" + "x" * (100 + 7 * i), priority=prio)
        for i, (name, prio) in enumerate(_LIVE_MODULES)
    ]


@pytest.mark.asyncio
async def test_priority_ties_break_on_name_so_the_prompt_is_deterministic():
    runtime = _runtime()
    ordered = _instructions()

    baseline = await runtime._build_module_instructions_prompt(ordered)

    rng = random.Random(20260728)
    for _ in range(20):
        shuffled = ordered[:]
        rng.shuffle(shuffled)
        assert await runtime._build_module_instructions_prompt(shuffled) == baseline


@pytest.mark.asyncio
async def test_awareness_socialnetwork_swap_is_a_same_length_reorder():
    """Guards the premise: the two blocks E2 identified really do swap at
    identical total length, i.e. no size-based diagnostic can catch it."""
    runtime = _runtime()
    ordered = _instructions()
    i_awareness = next(i for i, mi in enumerate(ordered) if mi.name == "AwarenessTasksModule")
    i_social = next(i for i, mi in enumerate(ordered) if mi.name == "SocialNetworkModule")
    assert ordered[i_awareness].priority == ordered[i_social].priority

    swapped = ordered[:]
    swapped[i_awareness], swapped[i_social] = swapped[i_social], swapped[i_awareness]

    a = await runtime._build_module_instructions_prompt(ordered)
    b = await runtime._build_module_instructions_prompt(swapped)
    assert len(a) == len(b)  # same-length: invisible to byte counts
    assert a == b  # ...and the total order makes it a no-op


def test_sort_key_is_total_over_priority_and_name():
    ordered = ContextRuntime._sorted_module_instructions(_instructions())
    keys = [(mi.priority, mi.name) for mi in ordered]
    assert keys == sorted(keys)
    # AwarenessTasks before SocialNetwork at the same priority (name order)
    assert keys.index((3, "AwarenessTasksModule")) < keys.index((3, "SocialNetworkModule"))


@pytest.mark.asyncio
async def test_turn_context_module_blocks_use_the_same_total_order(monkeypatch):
    """The turn block lands in the message rather than the prefix, but it uses
    the same total order so "module block order" means one thing everywhere."""
    from xyz_agent_context.schema import ContextData
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)

    class _Mod:
        def __init__(self, name: str, priority: int):
            self.config = SimpleNamespace(name=name, priority=priority)

        async def get_turn_context(self, ctx_data) -> str:
            return f"## {self.config.name} turn block"

    def _instances(order):
        return [
            SimpleNamespace(module_class=name, module=_Mod(name, prio), instance_id=f"i_{name}")
            for name, prio in order
        ]

    runtime = _runtime()
    ctx = ContextData(agent_id=runtime.agent_id, user_id=None, input_content="hi")

    a = await runtime._build_turn_context_block(_instances(_LIVE_MODULES), ctx, None)
    b = await runtime._build_turn_context_block(
        _instances(list(reversed(_LIVE_MODULES))), ctx, None
    )
    assert a == b


# =========================================================================
# Repository layer: the query must carry an explicit ORDER BY
# =========================================================================


class _RecordingDB:
    """Captures the kwargs BaseRepository.find hands to the DB client."""

    def __init__(self):
        self.calls: list[dict] = []

    async def get(self, table, filters=None, limit=None, offset=None, order_by=None):
        self.calls.append(
            {
                "table": table,
                "filters": filters,
                "limit": limit,
                "offset": offset,
                "order_by": order_by,
            }
        )
        return []


@pytest.mark.asyncio
async def test_get_public_instances_issues_an_order_by():
    db = _RecordingDB()
    repo = InstanceRepository(db)

    await repo.get_public_instances("agent_block_order")

    assert len(db.calls) == 1
    call = db.calls[0]
    assert call["filters"] == {"agent_id": "agent_block_order", "is_public": 1}
    # Explicit, and matching the siblings' convention (created_at DESC).
    assert call["order_by"] == "created_at DESC"


@pytest.mark.asyncio
async def test_get_public_instances_order_by_is_a_single_sortable_column():
    """The backends' order_by parser accepts ONE identifier + one ASC/DESC
    token; a comma-separated list would be silently mangled into an
    ascending single-column sort (db_backend_sqlite.get / _mysql.get)."""
    db = _RecordingDB()
    repo = InstanceRepository(db)

    await repo.get_public_instances("agent_block_order", module_class="ChatModule")

    order_by = db.calls[0]["order_by"]
    assert "," not in order_by
    parts = order_by.split()
    assert len(parts) == 2 and parts[1] in ("ASC", "DESC")
