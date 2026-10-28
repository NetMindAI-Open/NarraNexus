"""
@file_name: test_steer_routing.py
@author: Bin Liang
@date: 2026-08-21
@description: The orchestrator's steer decision — when a team lane is already
running, a new @mention is routed INTO the live run (steer_inbox + a push
onto its SteerChannel) and the processing cursor advances, instead of
waiting for the turn to end and dispatching a fresh one.

Steering only ever carries messages NEWER than what the running turn already
rendered (its ``_InFlight.rendered_through`` high-water); the turn's own trigger
batch is already in its prompt and must not be re-injected. And the processing
cursor is FORWARD-ONLY, so the turn's own end-ack cannot pull it back behind the
messages steering already delivered.

Uses the shared factory client (``get_db_client``) for the bus AND the
steer inbox, because that is what ``_route_steer`` reaches for — the two
must be one database, exactly as in production.
"""
from __future__ import annotations

import asyncio

import pytest

import narranexus.platform.agent_runtime.run_registry as run_registry
from narranexus.platform.agent_runtime.run_registry import RunRegistry, get_run_registry
from narranexus.platform.agent_runtime.steer_channel import SteerChannel
from narranexus.platform.message_bus.local_bus import LocalMessageBus, canonical_ts
from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger, _InFlight
from narranexus.platform.repository.steer_inbox_repository import (
    SteerInboxFull,
    SteerInboxRepository,
)
from narranexus.platform.utils.db.db_factory import get_db_client

ROOM = "ch_steer_route"
ME = "agent_steer_me"
RUN = "evt_steer_run1"

# A high-water BEFORE any message this test sends, so every sent message counts
# as "new" and is eligible to steer. Finding-A test overrides it to the future.
OLD_WATERMARK = "1970-01-01T00:00:00+00:00"


class _NullAudit:
    async def started(self, detail=None): ...
    async def stopped(self, detail=None): ...
    async def error(self, detail=None): ...
    async def heartbeat(self, detail=None, force=False): ...


async def _fresh_bus() -> LocalMessageBus:
    """A team room on the SHARED factory db, cleaned of any prior run's rows
    (the shared file db is session-scoped, so it can carry leftovers)."""
    db = await get_db_client()
    await db.execute("DELETE FROM bus_messages WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM bus_channel_members WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM bus_channels WHERE channel_id = %s", (ROOM,), fetch=False)
    await db.execute("DELETE FROM steer_inbox WHERE run_id = %s", (RUN,), fetch=False)
    # created_by = team_<id> marks a team room: delivery is pure @mention.
    await db.insert("bus_channels", {
        "channel_id": ROOM, "name": "room", "channel_type": "group",
        "created_by": "team_t1",
    })
    await db.insert("bus_channel_members", {"channel_id": ROOM, "agent_id": ME})
    return LocalMessageBus(db._backend)


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(run_registry, "_registry", RunRegistry())


def _trigger(bus) -> MessageBusTrigger:
    t = MessageBusTrigger(bus=bus, max_workers=3)
    t.audit = _NullAudit()
    return t


async def _dummy_task() -> asyncio.Task:
    """A live task object for _InFlight — _route_steer only reads
    rendered_through off the flight, never the task, so any task will do."""
    task = asyncio.ensure_future(asyncio.sleep(3600))
    return task


async def _make_live_lane(t: MessageBusTrigger, *, watermark: str = OLD_WATERMARK):
    """Put the lane in the state a running steerable turn leaves it: a live run
    in the registry AND an in-flight flight carrying the rendered high-water."""
    channel = SteerChannel(agent_id=ME)
    channel.run_id = RUN
    get_run_registry().register(ME, ROOM, RUN, channel)
    t._in_flight[(ME, ROOM)] = _InFlight(
        task=await _dummy_task(), started_at=0.0, rendered_through=watermark
    )
    return channel


def _drain_flight(t: MessageBusTrigger) -> None:
    flight = t._in_flight.get((ME, ROOM))
    if flight is not None:
        flight.task.cancel()


@pytest.mark.asyncio
async def test_route_steer_delivers_into_the_run_but_does_not_advance_the_cursor():
    # Delivery is a durable steer_inbox row + a push onto the live run. The cursor
    # is NOT advanced here — a push is not proof the run READ it (the turn may end
    # before its next drain), so acking on push would silently lose a
    # pushed-but-never-drained message. The cursor moves only on consumption.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="reconsider please",
            mentions=[ME],
        )

        await t._route_steer(ME, ROOM)

        injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        assert len(injs) == 1
        assert "reconsider please" in injs[0].content
        assert not channel.queue.empty()  # pushed onto the live run
        # created_at remembered so the consumption callback can name the cursor.
        assert channel._created_at  # non-empty

        # NOT acked on push — still pending until the run reports it consumed.
        remaining = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
        assert len(remaining) == 1
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_ack_steer_consumed_advances_processing_and_records_read_high_water():
    # The consumption side: once the run reports draining the steer rows,
    # _ack_steer_consumed marks them consumed, advances the PROCESSING cursor (so
    # the message is not re-delivered as a fresh turn), and RECORDS the read
    # high-water on the flight — but does NOT touch the read cursor here (that is
    # _ack_room_seen's job, which owns the gap protection). Delete the
    # ack_processed and pending stays; delete the steered_through record and the
    # read cursor can never catch up.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="please stop", mentions=[ME],
        )
        await t._route_steer(ME, ROOM)  # deliver (row + push), cursor NOT moved
        m = next(x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM)
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        watermark = channel._created_at[m.message_id]

        # The run drained it → consumption ack.
        await t._ack_steer_consumed(ME, ROOM, RUN, [m.message_id], watermark)

        # processing cursor advanced → no longer pending (no fresh-turn re-deliver)
        assert [x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM] == []
        # steer_inbox row stamped consumed (retention + back-pressure accounting)
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []
        # read cursor NOT advanced here — it is still counted unread; the flight
        # only carries the high-water for _ack_room_seen to use.
        assert await bus.count_unread(ME) == 1
        assert t._in_flight[(ME, ROOM)].steered_through == watermark
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_ack_room_seen_extends_read_to_steered_through_but_holds_on_a_gap():
    # The single gap-guarded read-cursor writer: _ack_room_seen advances the read
    # cursor to max(trigger, steered_through) when the window has no gap AND no
    # un-steered message slipped in; on an unsteered_gap it falls back to the
    # trigger so a never-rendered message is left unread.
    from narranexus.platform.message_bus.schemas import BusMessage

    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="steered", mentions=[ME])
        m = next(x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM)
        trig = BusMessage(message_id="trg", channel_id=ROOM, from_agent="usr_u1",
                          content="t", created_at="1970-01-01T00:00:00+00:00")
        flight = t._in_flight[(ME, ROOM)]
        flight.steered_through = canonical_ts(m.created_at)

        # No gap → read advances to steered_through (covers the steered message).
        await t._ack_room_seen(ME, ROOM, trig, is_team=True, rendered_from="1970-01-01T00:00:00+00:00")
        assert await bus.count_unread(ME) == 0

        # Now a gap: reset the read cursor by a fresh message + unsteered_gap set;
        # the read cursor must NOT jump over the un-rendered one.
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="never-shown", mentions=[])
        gap_msg = next(x for x in (await bus.get_recent_messages(ROOM, limit=10)) if x.content == "never-shown")
        flight.steered_through = canonical_ts(gap_msg.created_at)
        flight.unsteered_gap = True
        await t._ack_room_seen(ME, ROOM, trig, is_team=True, rendered_from="1970-01-01T00:00:00+00:00")
        # the never-shown message stays unread (read held at the trigger, which is
        # older than it).
        assert await bus.count_unread(ME) >= 1
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_a_busy_room_does_not_starve_live_steering_past_the_limit_window():
    # Important #1: un-addressed chatter must not permanently occupy the LIMIT-50
    # window and hide an @mention beyond it. _route_steer advances the processing
    # cursor over un-addressed messages older than any un-consumed steer (floor),
    # so a later cycle's window reaches the @mention and steers it. Delete the
    # un-addressed ack and the @mention beyond row 50 is never steered.
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        # 50 un-addressed (not @ ME) fill the whole LIMIT-50 window, then one
        # @mention sits at row 51 — invisible to the first scoped query.
        for i in range(50):
            await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                                   content=f"noise{i}", mentions=[])
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="@me please", mentions=[ME])

        # First cycle: window is all noise → nothing to steer, but the cursor
        # advances over the noise (floor is None — no un-consumed steer yet).
        await t._route_steer(ME, ROOM)
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []

        # Second cycle: the noise is acked away, so the @mention is now in-window
        # and gets steered.
        await t._route_steer(ME, ROOM)
        injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        assert [i.content for i in injs] == ["@me please"]
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_floored_unaddressed_ack_never_jumps_past_an_unconsumed_steer():
    # The floor guard — the ONLY branch that could lose a steer message. Steer an
    # @mention but do NOT consume it, then a NEWER un-addressed message arrives.
    # The floored un-addressed ack must NOT advance the processing cursor past the
    # un-consumed steered message, or that message would fall out of the pending
    # set and, if the turn ends before draining it, vanish. Assert on the CURSOR
    # (get_pending), not the inbox — the row exists either way. Delete the floor
    # (or `<`→`<=`) and this goes red.
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="@me act", mentions=[ME])
        await t._route_steer(ME, ROOM)  # steer the @mention (unconsumed)
        steered = next(x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM)

        # A NEWER un-addressed message arrives (not @ ME).
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="later noise", mentions=[])
        await t._route_steer(ME, ROOM)

        # The un-consumed steered @mention is still pending — the floor kept the
        # cursor from jumping past it via the newer un-addressed message.
        pend_ids = {x.message_id for x in await bus.get_pending_messages(ME)
                    if x.channel_id == ROOM}
        assert steered.message_id in pend_ids
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_floor_is_strict_an_unaddressed_message_equal_to_the_floor_is_not_acked():
    # Boundary (review Minor): the floor comparison is strict `<`, so an
    # un-addressed message with the SAME created_at as the oldest un-consumed
    # steered message must NOT be acked — else the cursor would land exactly on
    # the steered message's timestamp and drop it from the pending set. Second-
    # granularity created_at makes an exact tie reachable. `<`→`<=` → red here.
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    db = await get_db_client()
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="@me act", mentions=[ME])
        await t._route_steer(ME, ROOM)  # steer it (unconsumed → the floor)
        steered = next(x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM)

        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="tie noise", mentions=[])
        noise = next(x for x in await bus.get_recent_messages(ROOM, limit=10)
                     if x.content == "tie noise")
        # Force an exact created_at tie with the steered (floor) message.
        await db.execute(
            "UPDATE bus_messages SET created_at = %s WHERE message_id = %s",
            (steered.created_at, noise.message_id), fetch=False,
        )

        await t._route_steer(ME, ROOM)

        # The steered message stays pending — the tie was NOT acked past it.
        pend_ids = {x.message_id for x in await bus.get_pending_messages(ME)
                    if x.channel_id == ROOM}
        assert steered.message_id in pend_ids
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_skips_the_batch_if_the_run_released_mid_flight(monkeypatch):
    # The release-race guard: if the run ends and releases while _route_steer is
    # awaiting (get_pending / channel info / db), an append afterwards would write
    # a row nobody drains OR discards — a permanent leak. The re-check of
    # live_run(...) is run must skip the whole batch. Simulate the release landing
    # during the _get_channel_info await.
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="@me act", mentions=[ME])

        real_info = t._get_channel_info

        async def _release_then_info(cid):
            get_run_registry().release(RUN)  # the turn ended mid-_route_steer
            return await real_info(cid)

        monkeypatch.setattr(t, "_get_channel_info", _release_then_info)
        await t._route_steer(ME, ROOM)

        # Nothing appended for the released run — no orphan row written.
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []
        # And the message stays pending for a fresh turn (not acked away).
        assert [x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM]
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_release_race_still_raises_the_unsteered_gap_flag(monkeypatch):
    # The read-cursor gate input must survive the release-race early return: an
    # un-addressed message is seen, the run releases during the channel-info
    # await, _route_steer early-returns at the re-check — but unsteered_gap must
    # already be raised (it is set BEFORE the re-check), or _ack_room_seen would
    # extrapolate the read cursor past the never-rendered message. Move the flag
    # below the re-check → red.
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    flight = t._in_flight[(ME, ROOM)]
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="noise", mentions=[])  # un-addressed
        real_info = t._get_channel_info

        async def _release_then_info(cid):
            get_run_registry().release(RUN)
            return await real_info(cid)

        monkeypatch.setattr(t, "_get_channel_info", _release_then_info)
        await t._route_steer(ME, ROOM)

        assert flight.unsteered_gap is True  # raised before the early return
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_ack_room_seen_holds_read_when_a_steer_cycle_is_in_flight():
    # The default-conservative half of the gate: even with no unsteered_gap, an
    # in-flight _route_steer cycle (which may still discover an un-rendered
    # message after its awaits) blocks the read cursor from extrapolating to
    # steered_through. Drop the `steer_cycles_in_flight == 0` guard → red.
    from narranexus.platform.message_bus.schemas import BusMessage

    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    flight = t._in_flight[(ME, ROOM)]
    try:
        await bus.send_message(from_agent="usr_u1", to_channel=ROOM,
                               content="steered", mentions=[ME])
        m = next(x for x in await bus.get_pending_messages(ME) if x.channel_id == ROOM)
        flight.steered_through = canonical_ts(m.created_at)
        flight.unsteered_gap = False
        flight.steer_cycles_in_flight = 1  # a cycle is running
        trig = BusMessage(message_id="trg", channel_id=ROOM, from_agent="usr_u1",
                          content="t", created_at="1970-01-01T00:00:00+00:00")

        await t._ack_room_seen(ME, ROOM, trig, is_team=True,
                               rendered_from="1970-01-01T00:00:00+00:00")

        # Read cursor held (not extrapolated) → the steered message stays unread.
        assert await bus.count_unread(ME) == 1
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_start_loop_invokes_the_steer_cleanup_tick(monkeypatch):
    # Pins the WIRING itself: start()'s poll loop must call _maybe_run_steer_cleanup
    # each cycle (delete that call and steer_inbox is write-only again). Run one
    # loop iteration with everything else stubbed and stop after it.
    bus = await _fresh_bus()
    t = _trigger(bus)
    called: list = []

    async def _spy():
        called.append(1)

    async def _noop(*a, **k):
        return 0

    async def _stop_after():
        t._running = False

    monkeypatch.setattr(t, "_snapshot_wake_baseline", _noop)
    monkeypatch.setattr(t, "_poll_cycle", _noop)
    monkeypatch.setattr(t, "_check_worker_starvation", _noop)
    monkeypatch.setattr(t, "_maybe_run_steer_cleanup", _spy)
    monkeypatch.setattr(t, "_sleep_until_due", _stop_after)

    await t.start()

    assert called == [1]  # the loop ran the tick exactly once this iteration


@pytest.mark.asyncio
async def test_steer_cleanup_tick_actually_calls_the_repository_gated_daily(monkeypatch):
    # The retention tick must be WIRED — steer_inbox is this trigger's only
    # production writer, so if nobody calls cleanup the table is write-only. Test
    # the wiring (does _maybe_run_steer_cleanup call the repo), not just the repo
    # method. Delete the call in start()'s loop → the table grows forever; this
    # asserts the method itself calls through and is gated to daily.
    import narranexus.platform.message_bus.message_bus_trigger as mbt

    bus = await _fresh_bus()
    t = _trigger(bus)
    calls: list = []

    async def _spy(self, days, orphan_days=7):
        calls.append((days, orphan_days))
        return 0

    monkeypatch.setattr(SteerInboxRepository, "cleanup_older_than_days", _spy)

    # startup (_last is -inf) → runs, with the caller's retention constants.
    await t._maybe_run_steer_cleanup()
    assert calls == [(mbt.STEER_RETENTION_DAYS, mbt.STEER_ORPHAN_DAYS)]
    # immediately again → gated (one query a day, not per cycle).
    await t._maybe_run_steer_cleanup()
    assert len(calls) == 1
    # after the interval elapses → runs again.
    t._last_steer_cleanup_monotonic -= mbt.STEER_CLEANUP_INTERVAL_S + 1
    await t._maybe_run_steer_cleanup()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_route_steer_is_a_noop_when_the_run_is_not_registered():
    bus = await _fresh_bus()
    t = _trigger(bus)

    await bus.send_message(
        from_agent="usr_u1", to_channel=ROOM, content="hi", mentions=[ME],
    )

    await t._route_steer(ME, ROOM)

    remaining = await bus.get_pending_messages(ME)
    assert [m for m in remaining if m.channel_id == ROOM]  # still pending
    assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []


@pytest.mark.asyncio
async def test_route_steer_dedups_across_cycles():
    bus = await _fresh_bus()
    t = _trigger(bus)
    await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="once", mentions=[ME],
        )
        await t._route_steer(ME, ROOM)
        await t._route_steer(ME, ROOM)  # second pass re-sees before ack in a race

        injs = await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN)
        assert len(injs) == 1  # (run_id, msg_id) unique → injected at most once
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_does_not_reinject_the_turns_own_trigger_batch():
    # Finding A: while the turn runs, its trigger batch is unacked (the cursor
    # advances only at turn end), so get_pending_messages still returns it. A
    # steer that ignored the rendered high-water would re-inject the very batch
    # the turn is already acting on. With the watermark set AHEAD of the message
    # (as if the message were part of the rendered batch), it must be skipped.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t, watermark="2099-12-31T23:59:59+00:00")
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="the trigger message",
            mentions=[ME],
        )

        await t._route_steer(ME, ROOM)

        # Not steered: nothing in the inbox, nothing pushed, and the cursor was
        # NOT advanced (the running turn owns this batch and will ack it itself).
        assert await SteerInboxRepository(await get_db_client()).pull_unconsumed(RUN) == []
        assert channel.queue.empty()
        remaining = await bus.get_pending_messages(ME)
        assert [m for m in remaining if m.channel_id == ROOM]  # still pending
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_route_steer_delivers_the_prefix_and_leaves_the_rest_on_inbox_full(
    monkeypatch,
):
    # On SteerInboxFull mid-batch, the pushed prefix is delivered (into the run)
    # and the tail stays queued. No cursor is touched either way — delivery is
    # push, the cursor is consumption — so nothing is acked-away un-consumed.
    bus = await _fresh_bus()
    t = _trigger(bus)
    channel = await _make_live_lane(t)
    try:
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="first", mentions=[ME],
        )
        await bus.send_message(
            from_agent="usr_u1", to_channel=ROOM, content="second", mentions=[ME],
        )

        real_append = SteerInboxRepository.append
        calls = {"n": 0}

        async def _append_then_full(self, inj):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise SteerInboxFull("run backlog at capacity")
            return await real_append(self, inj)

        monkeypatch.setattr(SteerInboxRepository, "append", _append_then_full)

        await t._route_steer(ME, ROOM)

        # The first was pushed; the second was not. Neither is acked (cursor is
        # consumption-driven), so BOTH stay pending — the first until it is
        # consumed, the second until a later cycle delivers it.
        assert channel.queue.qsize() == 1
        remaining = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
        assert len(remaining) == 2
    finally:
        _drain_flight(t)


@pytest.mark.asyncio
async def test_ack_processed_only_moves_forward():
    # The core of the steer cursor fix: a turn's own end-ack (to its older
    # trigger high-water) must not pull the cursor BACK behind messages that
    # steering already acked forward, or those resurface as a fresh turn.
    bus = await _fresh_bus()

    await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="A", mentions=[ME])
    await bus.send_message(from_agent="usr_u1", to_channel=ROOM, content="B", mentions=[ME])
    pend = sorted(
        (m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM),
        key=lambda m: str(m.created_at),
    )
    older, newer = pend[0], pend[1]

    await bus.ack_processed(ME, ROOM, newer.created_at)   # steering acked to B
    await bus.ack_processed(ME, ROOM, older.created_at)   # turn end-ack to A (older)

    # The cursor stayed at B — the backward ack was a no-op, nothing resurfaced.
    remaining = [m for m in await bus.get_pending_messages(ME) if m.channel_id == ROOM]
    assert remaining == []


@pytest.mark.asyncio
async def test_the_daily_tick_also_sweeps_the_two_bus_ledgers(monkeypatch):
    """2026-09-09: receipts and owner-notice cooldowns are swept on the same
    tick, with retentions far above their 30-minute read windows."""
    import narranexus.platform.message_bus.message_bus_trigger as mbt
    from narranexus.platform.repository.bus_delivery_receipt_repository import (
        BusDeliveryReceiptRepository,
    )
    from narranexus.platform.repository.owner_notice_cooldown_repository import (
        OwnerNoticeCooldownRepository,
    )

    bus = await _fresh_bus()
    t = _trigger(bus)
    calls: list = []

    async def _steer(self, days, orphan_days=7):
        return 0

    async def _receipts(self, days):
        calls.append(("receipts", days))
        return 0

    async def _cooldowns(self, days):
        calls.append(("cooldowns", days))
        return 0

    monkeypatch.setattr(SteerInboxRepository, "cleanup_older_than_days", _steer)
    monkeypatch.setattr(BusDeliveryReceiptRepository, "cleanup_older_than_days", _receipts)
    monkeypatch.setattr(OwnerNoticeCooldownRepository, "cleanup_older_than_days", _cooldowns)

    await t._maybe_run_steer_cleanup()
    assert calls == [
        ("receipts", mbt.RECEIPT_RETENTION_DAYS),
        ("cooldowns", mbt.NOTICE_COOLDOWN_RETENTION_DAYS),
    ]
    assert mbt.NOTICE_COOLDOWN_RETENTION_DAYS * 86400 > mbt.FAILURE_NOTIFY_COOLDOWN_SECONDS * 10
    assert mbt.RECEIPT_RETENTION_DAYS * 86400 > mbt.FAILURE_NOTIFY_COOLDOWN_SECONDS * 10
