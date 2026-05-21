"""
@file_name: test_short_term_fairness.py
@author: Bin Liang
@date: 2026-05-20
@description: Cross-narrative short-term memory loading contract (Fix #2).

The 2026-05-11 per-instance fairness cap was REMOVED on 2026-05-20: short-term
memory is now PURE RECENCY — the latest SHORT_TERM_MAX_MESSAGES messages by
time across all other narratives, no per-instance reservation (Owner's call:
"只看时间顺序最新的"). Whatever falls off is reachable via the view_narrative tool.
Each returned message is tagged with its narrative_id, resolved from
instance_narrative_links (NOT off the record attribute — see below), so the
unified timeline can render [time · topic · nar_id].

REGRESSION GUARD (2026-05-20): InstanceRepository.get_chat_instances_by_user
returns BASE ModuleInstanceRecord objects, which have NO linked_narrative_ids
attribute (that field lives only on the ModuleInstance subclass and is not
populated by the SELECT). The fakes here are real ModuleInstanceRecord objects
so the test fails the way prod did if anyone reads the attribute off the record
again; the narrative mapping is supplied via the InstanceLinkRepository mock.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord


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


def _real_record(instance_id: str) -> ModuleInstanceRecord:
    # Exactly what get_chat_instances_by_user returns in prod: a base record
    # with NO linked_narrative_ids attribute.
    return ModuleInstanceRecord(
        instance_id=instance_id, module_class="ChatModule", agent_id="a_fair"
    )


def test_base_record_has_no_linked_narrative_ids():
    """Contract guard: the base record genuinely lacks the attribute, so the
    loader MUST resolve narratives via the links table, never off the record."""
    rec = _real_record("chat_x")
    assert not hasattr(rec, "linked_narrative_ids")


async def _run(chat_module, inst_nar_pairs, fake_memories):
    """inst_nar_pairs: ordered list of (instance_id, narrative_id)."""
    chat_module.event_memory_module.search_instance_json_format_memory = AsyncMock(
        side_effect=lambda module_name, inst_id: fake_memories.get(inst_id)
    )
    fake_instances = [_real_record(iid) for iid, _ in inst_nar_pairs]
    nar_map = dict(inst_nar_pairs)

    async def _get_nars(instance_id):
        nid = nar_map.get(instance_id)
        return [nid] if nid else []

    with patch(
        "xyz_agent_context.utils.db_factory.get_db_client",
        new=AsyncMock(return_value=MagicMock()),
    ), patch(
        "xyz_agent_context.repository.InstanceRepository.get_chat_instances_by_user",
        new=AsyncMock(return_value=fake_instances),
    ), patch(
        "xyz_agent_context.repository.instance_link_repository."
        "InstanceNarrativeLinkRepository.get_narratives_for_instance",
        new=AsyncMock(side_effect=_get_nars),
    ):
        return await chat_module._load_short_term_memory(
            module_name="ChatModule",
            exclude_instance_ids=["chat_current"],
        )


async def test_pure_recency_tags_narrative_and_keeps_all_under_cap(chat_module):
    """Total under the cap → all returned, time-sorted ascending, each tagged
    with its instance's narrative_id (resolved via the links table)."""
    inst_nar_pairs = [
        ("instance_A", "nar_A"),
        ("instance_B", "nar_B"),
        ("instance_C", "nar_C"),
    ]
    fake_memories = {
        "instance_A": {"messages": [_make_chat_msg(i, "instance_A", _ts(20 - i)) for i in range(5)]},
        "instance_B": {"messages": [_make_chat_msg(i, "instance_B", _ts(60 + i)) for i in range(3)]},
        "instance_C": {"messages": [_make_chat_msg(0, "instance_C", _ts(90))]},
    }
    result = await _run(chat_module, inst_nar_pairs, fake_memories)

    assert len(result) == 9  # 5 + 3 + 1, all under the 30 cap
    times = [m["meta_data"]["timestamp"] for m in result]
    assert times == sorted(times)  # ascending by time
    nar_by_inst = dict(inst_nar_pairs)
    for m in result:
        inst = m["meta_data"]["instance_id"]
        assert m["meta_data"]["narrative_id"] == nar_by_inst[inst]


async def test_cap_keeps_latest_by_time_no_fairness_reservation(chat_module):
    """Over the cap → the latest SHORT_TERM_MAX_MESSAGES by time win, even if a
    single recent thread fills the budget and older threads drop. No
    per-instance reservation (pure recency, not fairness)."""
    cap = ChatModule.SHORT_TERM_MAX_MESSAGES
    inst_nar_pairs = [
        ("instance_A", "nar_A"),  # chatty + most recent
        ("instance_B", "nar_B"),  # older
    ]
    fake_memories = {
        "instance_A": {"messages": [_make_chat_msg(i, "instance_A", _ts(i)) for i in range(cap + 10)]},
        "instance_B": {"messages": [_make_chat_msg(i, "instance_B", _ts(1000 + i)) for i in range(5)]},
    }
    result = await _run(chat_module, inst_nar_pairs, fake_memories)

    assert len(result) == cap
    assert all(m["meta_data"]["instance_id"] == "instance_A" for m in result)
    assert all(m["meta_data"]["narrative_id"] == "nar_A" for m in result)
