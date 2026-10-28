"""
@file_name: test_inbox_recorder.py
@author:
@date: 2026-08-17
@description: The inbox record must not touch the bus, and a turn must stay
inbound-then-reply.

`ChannelInboxWriter` wrote its five-row bundle into the MessageBus tables and,
in doing so, put every IM message on the agent's bus unread cursor. The
recorder replaces it. Two properties carry the whole point:

  * it writes ONLY `inbox_*` tables — the containment is structural, not a
    prefix filter (a filter is what drifted in the 2026-07-03 incident);
  * a turn's two rows keep the one-microsecond stagger that orders them.
"""
from __future__ import annotations

import asyncio
import json

from narranexus.platform.channel.inbox_recorder import (
    AGENT_DM_THREAD_PREFIX,
    IM_THREAD_PREFIX,
    INBOUND,
    OUTBOUND,
    InboxRecorder,
    agent_dm_thread_id,
    im_thread_id,
)


class FakeDB:
    """Records every write so a test can assert on WHICH tables were touched."""

    def __init__(self, existing=None):
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        self._existing = existing or {}

    async def get_one(self, table, filters):
        return self._existing.get(table)

    async def insert(self, table, data):
        self.inserts.append((table, data))

    async def update(self, table, filters, data):
        self.updates.append((table, filters, data))


def _record(db, **kw):
    rec = InboxRecorder("lark", "Feishu")
    defaults = dict(
        db=db,
        thread_id=im_thread_id("lark", "agent_a", "oc_1"),
        owner_user_id="usr_1",
        agent_id="agent_me",
        counterpart_id="ou_zhang",
        counterpart_name="张三",
        inbound_text="在吗",
        outbound_text="在的",
    )
    defaults.update(kw)
    asyncio.run(rec.record_turn(**defaults))


def test_the_recorder_never_writes_a_bus_table():
    """The whole decoupling, as one assertion.

    A membership row in `bus_channel_members` is what put 1,364 IM messages on
    90 agents' unread cursor. If this test ever fails, that leak is back.
    """
    db = FakeDB()
    _record(db)

    touched = {t for t, _ in db.inserts} | {t for t, _, _ in db.updates}
    assert touched <= {"inbox_threads", "inbox_thread_messages"}, touched
    assert not any(t.startswith("bus_") for t in touched)


def test_a_turn_is_two_rows_inbound_first():
    db = FakeDB()
    _record(db)

    msgs = [d for t, d in db.inserts if t == "inbox_thread_messages"]
    assert [m["direction"] for m in msgs] == [INBOUND, OUTBOUND]
    # created_at alone must order the turn — the reply used to be able to sort
    # above the message it answered when both shared a microsecond.
    assert msgs[1]["created_at"] > msgs[0]["created_at"]


def test_a_silent_turn_records_only_what_arrived():
    """A turn that legitimately said nothing must not leave an empty bubble."""
    db = FakeDB()
    _record(db, outbound_text="")

    msgs = [d for t, d in db.inserts if t == "inbox_thread_messages"]
    assert [m["direction"] for m in msgs] == [INBOUND]


def test_the_thread_is_created_once_and_carries_the_owner():
    db = FakeDB()
    _record(db)

    threads = [d for t, d in db.inserts if t == "inbox_threads"]
    assert len(threads) == 1
    assert threads[0]["owner_user_id"] == "usr_1"
    assert threads[0]["source"] == "lark"
    assert threads[0]["title"] == "Feishu: 张三"


def test_an_existing_thread_gets_its_placeholder_name_replaced():
    """A sender first seen as "Unknown" fell back to the raw id; without the
    refresh the panel shows that id forever, for the whole first burst from
    every new contact."""
    db = FakeDB(existing={"inbox_threads": {
        "thread_id": im_thread_id("lark", "agent_a", "oc_1"),
        "counterpart_name": "ou_zhang",
    }})
    _record(db)

    assert not [d for t, d in db.inserts if t == "inbox_threads"]
    name_updates = [
        d for t, _, d in db.updates
        if t == "inbox_threads" and d.get("counterpart_name")
    ]
    assert name_updates and name_updates[0]["counterpart_name"] == "张三"


def test_attachments_survive_as_json():
    db = FakeDB()
    _record(db, inbound_attachments=[{"file_id": "att_1", "original_name": "报告.pdf"}])

    inbound = next(d for t, d in db.inserts if t == "inbox_thread_messages")
    assert json.loads(inbound["attachments"])[0]["original_name"] == "报告.pdf"


def test_thread_ids_declare_their_family_first():
    """`im_` / `nx_dm_` — the namespace says WHAT before it says WHICH, so any
    residual filter is trivially correct instead of a channel-name list that
    goes stale (which is exactly what `im_channel_prefixes` did)."""
    assert im_thread_id("lark", "agent_a", "oc_1").startswith(IM_THREAD_PREFIX)
    # The agent id sits between the channel and the chat: the namespace still
    # says WHAT first, and the KEY now says which agent, not only which chat.
    assert im_thread_id("wechat", "agent_a", "wx_9") == "im_wechat_agent_a_wx_9"
    assert agent_dm_thread_id("agent_a", "agent_b").startswith(AGENT_DM_THREAD_PREFIX)


def test_a_peer_thread_is_keyed_by_both_agents():
    """One owner can have several agents talking to the same peer, and the
    panel lists per agent — keying on the peer alone would merge them."""
    a = agent_dm_thread_id("agent_a", "agent_peer")
    b = agent_dm_thread_id("agent_b", "agent_peer")
    assert a != b
