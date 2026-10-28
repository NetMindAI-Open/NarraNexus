"""
@file_name: test_inbox_recorder_db.py
@author:
@date: 2026-08-17
@description: The recorder against a real database — repeat-safety and the
guards that survived the move off the bus tables.

Replaces `test_channel_inbox_writer.py`. Most of what that file pinned is now
in `test_inbox_recorder.py` (fake-db, faster): the thread is created once, a
placeholder name is refreshed, a silent turn writes one row, thread ids carry
their family prefix. Two guards had no home in that file and are relocated
here rather than dropped:

  * repeat-write safety — the old writer's "get, then insert only when
    missing" shape is easy to lose in a refactor and the symptom (a duplicated
    thread per inbound message) is only visible against a real DB;
  * the constructor's rejection of an empty source.

Deliberately NOT carried over: assertions about `bus_agent_registry`,
`bus_channels` and `bus_channel_members` rows. Those rows are the thing this
change removes — `test_inbox_recorder.py::test_the_recorder_never_writes_a_bus_table`
is now the guard that they stay gone.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.inbox_recorder import InboxRecorder, im_thread_id


async def _record(db, *, original="hello", response="hi back",
                  channel="slack", brand="Slack", chat_id="C_room",
                  agent_id="agent_a", sender_id="U_alice", sender_name="Alice"):
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


@pytest.mark.asyncio
async def test_recording_twice_reuses_the_thread_and_appends_messages(db_client):
    """One thread, four messages.

    The old writer created its thread-equivalent with "read first, insert only
    when missing", and a refactor that loses that shape duplicates a row per
    inbound message — invisible in a fake-db test, obvious here.
    """
    await _record(db_client)
    await _record(db_client)

    thread_id = im_thread_id("slack", "agent_a", "C_room")
    threads = await db_client.get("inbox_threads", {"thread_id": thread_id})
    assert len(threads) == 1, f"thread duplicated: {threads!r}"

    msgs = await db_client.get("inbox_thread_messages", {"thread_id": thread_id})
    assert len(msgs) == 4, "each turn appends one inbound and one outbound row"


@pytest.mark.asyncio
async def test_the_thread_row_carries_the_owner_and_the_brand_title(db_client):
    await _record(db_client)

    thread = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", "agent_a", "C_room")}
    )
    assert thread["owner_user_id"] == "usr_owner"
    assert thread["agent_id"] == "agent_a"
    assert thread["source"] == "slack"
    assert thread["title"] == "Slack: Alice"


@pytest.mark.asyncio
async def test_two_channels_with_the_same_chat_id_do_not_collide(db_client):
    """Some platforms emit short numeric chat ids; an unprefixed thread id
    would merge a Slack conversation into a Telegram one."""
    await _record(db_client, channel="slack", brand="Slack", chat_id="123")
    await _record(db_client, channel="telegram", brand="Telegram", chat_id="123")

    slack = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", "agent_a", "123")}
    )
    tg = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("telegram", "agent_a", "123")}
    )
    assert slack["title"] == "Slack: Alice"
    assert tg["title"] == "Telegram: Alice"


def test_the_recorder_rejects_an_empty_source():
    """The source is identity — it names the thread and matches the registry.

    An empty brand is allowed and falls back to a title-cased source: the brand
    is a display label, and refusing to record a turn because nobody supplied a
    pretty name would lose the conversation over cosmetics. The old writer
    rejected both; only the identity half is worth a hard failure.
    """
    with pytest.raises(ValueError):
        InboxRecorder("", "Slack")

    assert InboxRecorder("slack", "")._brand == "Slack"


@pytest.mark.asyncio
async def test_two_turns_opening_the_same_new_thread_both_land(db_client):
    """Losing the create race must not lose a message.

    `_ensure_thread` was read-then-insert on a `thread_id` primary key, so two
    turns opening the same NEW thread both saw `None` and both inserted. The
    loser raised, `record_turn` re-raised, and the caller booked
    `EVENT_INBOX_WRITE_FAILED` — the message simply absent from the user's
    panel, with the turn itself perfectly successful.

    Not a theoretical race: debounce batches and multi-agent group chats deliver
    concurrently, and the window is the whole gap between the read and the
    insert. Driven with `asyncio.gather` so both coroutines are inside that
    window rather than by patching the shape.
    """
    import asyncio

    await asyncio.gather(
        _record(db_client, original="first", response="r1", sender_name="Alice"),
        _record(db_client, original="second", response="r2", sender_name="Alice"),
    )

    threads = await db_client.get("inbox_threads", {})
    assert len(threads) == 1, "the race created a second thread"

    msgs = await db_client.get(
        "inbox_thread_messages", {"thread_id": im_thread_id("slack", "agent_a", "C_room")}
    )
    bodies = {m["content"] for m in msgs}
    assert {"first", "second", "r1", "r2"} <= bodies, (
        f"a turn's rows were lost to the create race: {sorted(bodies)}"
    )


@pytest.mark.asyncio
async def test_a_create_that_fails_for_a_real_reason_still_raises(db_client):
    """The swallow is scoped to "the row exists now", nothing wider.

    The duplicate-key exception type differs between aiosqlite and aiomysql, so
    the handler re-reads instead of matching on the driver's error class. That
    makes it important that a MISSING row still propagates — otherwise the
    handler would quietly absorb every insert failure and the inbox would lose
    messages with no audit event at all, which is worse than the bug it fixes.
    """
    from narranexus.platform.channel.inbox_recorder import InboxRecorder

    rec = InboxRecorder("slack", "Slack")

    class _Db:
        async def get_one(self, *_a, **_k):
            return None  # never there, before or after

        async def insert(self, *_a, **_k):
            raise RuntimeError("disk is on fire")

    with pytest.raises(RuntimeError, match="disk is on fire"):
        await rec._ensure_thread(
            _Db(), thread_id="t", owner_user_id="u", agent_id="a",
            counterpart_id="c", counterpart_name="C", now="2026-08-18",
        )


def test_no_call_site_hands_the_silence_sentinel_to_the_recorder():
    """A silent turn writes no outbound row — asserted at the CALL SITES.

    `record_turn`'s contract is that an empty `outbound_text` writes nothing,
    and `test_inbox_recorder.py` pins the recorder's half. The defect was one
    layer up: the managed path passed `(reply_text or "").strip() or
    CHANNEL_SILENT_SENTINEL`, so silence arrived as a non-empty string and the
    recorder dutifully wrote `(stayed silent)` as an OUTBOUND row attributed to
    the agent — while the other call site, three hundred lines away, passed the
    text as-is. Telegram and WeChat read `inbox_thread_messages` as their
    conversation memory, so that string came back to the agent as its own
    previous reply; `_platform_reply_text`'s own docstring names this failure.

    Source-level because the recorder cannot see it: from inside, a sentinel and
    a real reply are both just non-empty text, which is why the behavioural
    tests were green throughout.
    """
    import inspect

    from narranexus.platform.channel import channel_trigger_base

    src = inspect.getsource(channel_trigger_base)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "outbound_text=" in stripped:
            assert "CHANNEL_SILENT_SENTINEL" not in stripped, (
                f"a call site sends the silence sentinel into the inbox, where "
                f"the agent reads it back as its own words: {stripped}"
            )


@pytest.mark.asyncio
async def test_two_agents_on_the_same_chat_do_not_share_a_thread(db_client):
    """The dimension `test_two_channels_with_the_same_chat_id_do_not_collide` missed.

    That one varies the CHANNEL. This varies the AGENT, which is the case that
    actually collided: `im_thread_id` was `im_<channel>_<chat_id>`, so one chat
    produced one thread row, and `agent_id` was a column set once at creation and
    never updated (`_ensure_thread` returns early on an existing row). The panel
    filters on that column, so agent B's messages appended to agent A's thread and
    **B's inbox was empty**.

    Not theoretical: a Telegram private chat's `chat_id` is the USER's id,
    identical across bots, so one person DMing two of an owner's agents hit it.
    The writer this replaced created a `bus_channel_members` row per agent, so both
    sides were visible — this was a regression.

    It also has to hold before anything scopes BY agent: `wipe_service`'s "clear
    this agent's conversations" would otherwise delete the other agent's record.
    """
    await _record(db_client, agent_id="agent_a", original="to A", response="from A")
    await _record(db_client, agent_id="agent_b", original="to B", response="from B")

    threads = await db_client.get("inbox_threads", {})
    assert len(threads) == 2, (
        f"two agents on one chat produced {len(threads)} thread(s) — the second "
        f"agent's inbox is invisible"
    )
    assert {t["agent_id"] for t in threads} == {"agent_a", "agent_b"}

    # Each agent's panel query returns only its own side.
    for agent, mine, theirs in (
        ("agent_a", "to A", "to B"),
        ("agent_b", "to B", "to A"),
    ):
        rows = await db_client.get("inbox_threads", {"agent_id": agent})
        assert len(rows) == 1, f"{agent} sees {len(rows)} threads"
        msgs = await db_client.get(
            "inbox_thread_messages", {"thread_id": rows[0]["thread_id"]}
        )
        bodies = {m["content"] for m in msgs}
        assert mine in bodies and theirs not in bodies, (
            f"{agent}'s thread contains the other agent's messages: {bodies}"
        )
