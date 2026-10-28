"""
@file_name: test_wechat_context_builder.py
@date: 2026-08-19
@description: WeChat deploy-window fallback read — the WeChat half of C3.

WeChat's ``_legacy_bus_history`` is the same code as Telegram's, and iLink has no
history API, so the local record IS the agent's whole memory. This gives the
WeChat copy its own regression net (a future edit or the runbook's delete step
must touch BOTH), and pins the agent-isolation the fallback depends on.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from narranexus_plugins.wechat_module.wechat_context_builder import (
    WeChatContextBuilder,
)
from narranexus.platform.schema.parsed_message import ParsedMessage


class _TableAwareDB:
    """Returns different canned rows per table, so the fallback (new inbox table
    empty → read legacy ``bus_messages``) can be exercised."""

    def __init__(self, by_table):
        self._by_table = by_table
        self.calls = []

    async def get(self, table, filters=None, limit=None, offset=None, order_by=None):
        self.calls.append((table, filters, limit, order_by))
        return list(self._by_table.get(table, []))


def _builder(db, *, chat_id="wx_1", message_id="cur", agent_id="agent_a"):
    msg = ParsedMessage(
        message_id=message_id, chat_id=chat_id, sender_id="u", sender_name="U",
    )
    return WeChatContextBuilder(
        message=msg, credential=MagicMock(), agent_id=agent_id, db_client=db,
    )


@pytest.mark.asyncio
async def test_history_falls_back_to_legacy_bus_when_inbox_empty():
    """New inbox table empty → read pre-decouple turns from bus_messages,
    AGENT-ISOLATED. Remove the fallback and this goes red; drop the from_agent
    isolation and agent_b's line leaks into agent_a's memory."""
    chat_id = "wx_42"
    legacy_newest_first = [
        {  # current trigger — dropped
            "message_id": "cur", "channel_id": f"wechat_{chat_id}",
            "from_agent": "wechat_user_1", "content": "再试一下",
            "created_at": "2026-05-13 17:31:26",
        },
        {  # another bot in the same shared wechat_<chat_id> channel — excluded
            "message_id": "b_other", "channel_id": f"wechat_{chat_id}",
            "from_agent": "agent_b", "content": "not my reply",
            "created_at": "2026-05-13 17:20:00",
        },
        {  # this bot's own reply
            "message_id": "b_2", "channel_id": f"wechat_{chat_id}",
            "from_agent": "agent_a", "content": "在的",
            "created_at": "2026-05-13 17:06:57",
        },
        {  # the user
            "message_id": "b_1", "channel_id": f"wechat_{chat_id}",
            "from_agent": "wechat_user_1", "content": "在吗",
            "created_at": "2026-05-13 17:06:55",
        },
    ]
    db = _TableAwareDB({
        "inbox_thread_messages": [],
        "bus_messages": legacy_newest_first,
    })
    builder = _builder(db, chat_id=chat_id, message_id="cur")

    history = await builder.get_conversation_history(limit=10)

    bodies = [h["body"] for h in history]
    assert bodies == ["在吗", "在的"]
    assert "not my reply" not in bodies
    assert {h["body"]: h["sender"] for h in history}["在的"] == "Me (bot)"
    assert [c[0] for c in db.calls] == ["inbox_thread_messages", "bus_messages"]


@pytest.mark.asyncio
async def test_history_does_not_fall_back_when_inbox_has_rows():
    """The fallback must NOT fire when the new table already has history."""
    chat_id = "wx_99"
    db = _TableAwareDB({
        "inbox_thread_messages": [{
            "message_id": "n1", "direction": "in", "sender_name": "U",
            "content": "new-world", "created_at": "2026-05-13 17:00:00",
        }],
        "bus_messages": [{
            "message_id": "old", "channel_id": f"wechat_{chat_id}",
            "from_agent": "agent_a", "content": "legacy",
            "created_at": "2026-05-13 16:00:00",
        }],
    })
    builder = _builder(db, chat_id=chat_id, message_id="cur")

    history = await builder.get_conversation_history(limit=10)
    assert [h["body"] for h in history] == ["new-world"]
    assert [c[0] for c in db.calls] == ["inbox_thread_messages"]
