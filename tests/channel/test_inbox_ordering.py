"""
@file_name: test_inbox_ordering.py
@author: Bin Liang
@date: 2026-07-03
@description: Inbox message ordering — the inbound row must always sort before
              its reply, and turns must sort in completion order.

Root cause of the "messages out of order" report (worst on WeChat): the inbox
writer computed ``now = utc_now()`` ONCE and wrote
both the inbound and the outbound row with that identical timestamp. With
``ORDER BY created_at`` and no tie-break, two rows sharing a microsecond
sort in an unstable order — the reply could render above the message it
answered. WeChat is worst because it has no per-message timestamp at all
(timestamp_ms == 0), so nothing downstream could disambiguate.

Fix: the recorder stamps the inbound row and the reply row with two strictly
increasing timestamps, so ``created_at`` alone orders a turn correctly, and
completion order sorts turns.

Moved to the inbox's own tables 2026-08-17 with the writer it guards. The
property is unchanged and so is the incident behind it — only the table did.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.inbox_recorder import InboxRecorder, im_thread_id


async def _write(db, *, original, response, chat_id="C_room", channel="wechat",
                 brand="WeChat", agent_id="agent_a", sender_id="U_alice",
                 sender_name="Alice"):
    await InboxRecorder(channel, brand).record_turn(
        db=db,
        thread_id=im_thread_id(channel, agent_id, chat_id),
        owner_user_id="usr_owner",
        agent_id=agent_id,
        counterpart_id=sender_id,
        counterpart_name=sender_name,
        inbound_text=original,
        outbound_text=response,
    )


async def _rows_in_order(db, thread_id):
    rows = await db.get("inbox_thread_messages", {"thread_id": thread_id})
    return sorted(rows, key=lambda r: (str(r["created_at"]), r["message_id"]))


@pytest.mark.asyncio
async def test_inbound_row_sorts_before_its_reply(db_client):
    await _write(db_client, original="你好啊", response="大西瓜你好")
    rows = await _rows_in_order(db_client, im_thread_id("wechat", "agent_a", "C_room"))
    assert len(rows) == 2
    inbound, outbound = rows[0], rows[1]
    assert inbound["content"] == "你好啊", "inbound must sort first"
    assert outbound["content"] == "大西瓜你好"
    assert str(inbound["created_at"]) < str(outbound["created_at"]), (
        "inbound and reply must not share a timestamp"
    )


@pytest.mark.asyncio
async def test_two_turns_interleave_correctly(db_client):
    await _write(db_client, original="Q1", response="A1")
    await _write(db_client, original="Q2", response="A2")
    contents = [r["content"] for r in await _rows_in_order(db_client, im_thread_id("wechat", "agent_a", "C_room"))]
    assert contents == ["Q1", "A1", "Q2", "A2"], (
        "turns must read as Q1 A1 Q2 A2, not interleaved or reversed"
    )


@pytest.mark.asyncio
async def test_created_at_strictly_increases_across_all_rows(db_client):
    for i in range(3):
        await _write(db_client, original=f"Q{i}", response=f"A{i}")
    rows = await _rows_in_order(db_client, im_thread_id("wechat", "agent_a", "C_room"))
    stamps = [str(r["created_at"]) for r in rows]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps), "every row must have a distinct timestamp"


@pytest.mark.asyncio
async def test_a_silent_turn_stamps_the_thread_at_its_only_message(db_client):
    """`last_message_at` must name a row that exists.

    Both rows of a turn are written from one `now`, one microsecond apart, so the
    reply sorts after the message it answers. The thread's `last_message_at` was
    stamped at the REPLY slot unconditionally — so a silent turn, which has no row
    there, claimed a last message one microsecond after the only message it has.

    The panel sorts threads on this column, so the visible symptom is a silent
    turn sorting ahead of a turn that actually answered at the same instant.
    """
    await _write(db_client, original="just asking", response="", chat_id="C_silent")

    tid = im_thread_id("wechat", "agent_a", "C_silent")
    thread = await db_client.get_one("inbox_threads", {"thread_id": tid})
    rows = await _rows_in_order(db_client, tid)

    assert len(rows) == 1, f"a silent turn wrote {len(rows)} rows"
    assert str(thread["last_message_at"]) == str(rows[0]["created_at"]), (
        f"the thread's last_message_at ({thread['last_message_at']}) names no "
        f"row; its only message is at {rows[0]['created_at']}"
    )
    assert thread["last_message_preview"] == "just asking"
