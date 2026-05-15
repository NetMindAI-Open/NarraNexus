"""
@file_name: test_short_term_fairness.py
@author: Bin Liang
@date: 2026-05-11
@description: Per-instance fairness for short-term memory.

Without the per-instance cap, one chatty cross-narrative ChatModule
instance can saturate the entire SHORT_TERM_MAX_MESSAGES=15 budget and
starve every other narrative — meaning the agent never sees rows from
the other 4 / 10 / 100 narratives the user has touched, even though
they may be exactly the context the current turn needs.

The two-stage budgeting (Stage A per-instance cap, Stage B global cap)
fixes that. This test pins the contract.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def chat_module(db_client):
    return ChatModule(
        agent_id="a_fair",
        user_id="u_fair",
        database_client=db_client,
        instance_id="chat_current",
    )


@pytest.mark.asyncio
async def test_one_chatty_instance_does_not_starve_others(chat_module):
    """Three other instances:
       - instance_A: 20 recent chat rows (chatty)
       - instance_B: 3 chat rows (sparse)
       - instance_C: 1 chat row (sparser)
    Pre-fix: short_term would be 15 rows all from instance_A (it's the
    most recent across all instances). Post-fix: instance_A capped at
    5, leaving room for instance_B and instance_C to be represented."""

    def _make_chat_msg(idx: int, instance_id: str, ts: str) -> dict:
        return {
            "role": "user" if idx % 2 == 0 else "assistant",
            "content": f"msg #{idx} from {instance_id}",
            "meta_data": {
                "event_id": f"evt_{instance_id}_{idx}",
                "timestamp": ts,
                "instance_id": instance_id,
                "working_source": "chat",
            },
        }

    # Build payloads. instance_A's rows are all "more recent" than B/C
    # to make the test bite — without per-instance cap they would all
    # win the global recency sort and crowd everything else out.
    instance_a_msgs = [
        _make_chat_msg(i, "instance_A", _ts(20 - i))  # 0..20 min ago
        for i in range(20)
    ]
    instance_b_msgs = [
        _make_chat_msg(i, "instance_B", _ts(60 + i))  # 60..62 min ago
        for i in range(3)
    ]
    instance_c_msgs = [
        _make_chat_msg(0, "instance_C", _ts(90))
    ]

    # Mock the two external dependencies of _load_short_term_memory.
    fake_instances = [
        type("FI", (), {"instance_id": "instance_A"})(),
        type("FI", (), {"instance_id": "instance_B"})(),
        type("FI", (), {"instance_id": "instance_C"})(),
    ]
    fake_memories = {
        "instance_A": {"messages": instance_a_msgs},
        "instance_B": {"messages": instance_b_msgs},
        "instance_C": {"messages": instance_c_msgs},
    }

    # The function does both `await get_db_client()` and `InstanceRepository(db_client)`
    # inside its body. We patch:
    #   (a) get_db_client → return our in-memory chat_module.database_client
    #       so it doesn't try to dial MySQL
    #   (b) InstanceRepository.get_chat_instances_by_user → return our
    #       fake instances list (no DB read at all)
    chat_module.event_memory_module.search_instance_json_format_memory = AsyncMock(
        side_effect=lambda module_name, inst_id: fake_memories.get(inst_id)
    )

    fake_db = MagicMock()

    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=fake_db),
    ), patch(
        "xyz_agent_context.repository.InstanceRepository.get_chat_instances_by_user",
        new=AsyncMock(return_value=fake_instances),
    ):
        result = await chat_module._load_short_term_memory(
            module_name="ChatModule",
            exclude_instance_ids=["chat_current"],
        )

    # Tally per-instance contributions.
    by_inst: dict[str, int] = {}
    for msg in result:
        inst = msg["meta_data"]["instance_id"]
        by_inst[inst] = by_inst.get(inst, 0) + 1

    # Global cap respected.
    assert len(result) <= ChatModule.SHORT_TERM_MAX_MESSAGES

    # Per-instance cap respected — this is the fairness property.
    for inst, n in by_inst.items():
        assert n <= ChatModule.SHORT_TERM_PER_INSTANCE, (
            f"instance {inst} contributed {n} rows, "
            f"exceeds SHORT_TERM_PER_INSTANCE={ChatModule.SHORT_TERM_PER_INSTANCE}"
        )

    # Sparse instances must NOT be starved out — both B and C should
    # appear despite instance_A's flood.
    assert "instance_B" in by_inst, f"instance_B starved out: {by_inst!r}"
    assert "instance_C" in by_inst, f"instance_C starved out: {by_inst!r}"
