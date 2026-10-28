"""
@file_name: test_bus_circuit_breaker_gate.py
@author:
@date: 2026-07-13
@description: MessageBusTrigger._process_lane circuit-breaker skip-gate.

A paused/cooling agent must be skipped WITHOUT consuming its pending bus
messages (they stay queued for when it resumes). The gate lives in the
production per-lane method and fires before the bus read.

Half-open probe (GitHub #117 review): ``should_skip`` is a pure read; the
single probe grant is claimed by ``try_begin_probe`` only at the point a
turn is actually about to run — AFTER the @mention filter and rate-limit
ack-and-return branches — so the 3s poller cannot burn the grant on a batch
it never runs. A refused claim leaves the batch queued.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import narranexus.platform.agent_framework.loop.circuit_breaker as cb
from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger
from narranexus.platform.message_bus.schemas import BusMessage


class _SpyBus:
    """Records whether the trigger tried to read pending messages, serves a
    fixed batch, and records acks."""
    def __init__(self, messages=None):
        self.get_pending_called = False
        self.messages = messages or []
        self.acks = []

    async def get_pending_messages(self, agent_id, limit=50, channel_id=None):
        self.get_pending_called = True
        return list(self.messages)

    async def ack_processed(self, agent_id, channel_id, created_at):
        self.acks.append((agent_id, channel_id, created_at))


_SEQ = iter(range(1, 10_000))


def _msg(from_agent="peer", mentions=None, created_at=None, **parts):
    """A real BusMessage (the production type): the lane runs multipart
    assembly on the batch, which reads the part_* fields — a thinner double
    would hide exactly that step."""
    n = next(_SEQ)
    return BusMessage(
        message_id=f"m_{n}",
        channel_id="ch_room",
        from_agent=from_agent,
        content=f"hello {n}",
        mentions=mentions,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        **parts,
    )


def _trigger(bus, channel_type="group", channel_owner="owner"):
    t = MessageBusTrigger.__new__(MessageBusTrigger)
    t._semaphore = asyncio.Semaphore(10)
    t._lane_locks = {}
    # `_process_lane` marks its dispatch as slot-holding for the heartbeat;
    # called directly there is no dispatch, so an empty registry is the
    # accurate state, not a stub.
    t._in_flight = {}
    t._bus = bus
    t._rate_counters = {}
    t.batches = []
    t.probe_tokens = []

    async def _channel_info(channel_id):
        return (channel_type, channel_owner)

    async def _batch(agent_id, channel_id, messages, trigger_msg, channel_owner="",
                     probe_token=None):
        t.batches.append((agent_id, channel_id, messages))
        t.probe_tokens.append(probe_token)

    t._get_channel_info = _channel_info
    t._handle_channel_batch = _batch
    return t


def _probe_spy(monkeypatch, allowed=True):
    """Stub the claim and count how often the lane tried to take it."""
    calls = []

    async def fake_probe(agent_id, db=None, *, prior=None):
        calls.append(agent_id)
        if allowed:
            return cb.TurnAdmission(allowed=True, reason=None, probe_token="tok-lane")
        return cb.TurnAdmission(allowed=False, reason="probing")
    monkeypatch.setattr(cb, "try_begin_probe", fake_probe)
    return calls


@pytest.mark.asyncio
async def test_paused_agent_skipped_without_touching_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return cb.GateVerdict(skip=True, reason="paused:auth")
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_lane("ag_paused", "ch_test")

    assert result is False
    assert bus.get_pending_called is False  # messages left queued


@pytest.mark.asyncio
async def test_healthy_agent_falls_through_to_bus(monkeypatch):
    async def fake_skip(agent_id, db=None):
        return cb.GateVerdict(skip=False, reason=None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)

    bus = _SpyBus()
    t = _trigger(bus)
    result = await t._process_lane("ag_ok", "ch_test")

    # No pending messages → returns False, but it DID consult the bus.
    assert result is False
    assert bus.get_pending_called is True


@pytest.mark.asyncio
async def test_mention_filtered_batch_does_not_claim_the_probe(monkeypatch):
    """Group room, the agent is not @mentioned: the batch is acked without a
    turn — and the probe grant must NOT have been taken on the way."""
    async def fake_skip(agent_id, db=None):
        # PAUSED but window open: the read gate passes
        return cb.GateVerdict(skip=False, reason=None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch)

    bus = _SpyBus([_msg(mentions=["someone_else"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert bus.acks  # cursor advanced past the irrelevant batch
    assert probe_calls == []  # grant untouched
    assert t.batches == []


@pytest.mark.asyncio
async def test_refused_probe_leaves_relevant_batch_queued(monkeypatch):
    """A relevant batch whose claim loses the race is NOT run and NOT acked —
    it stays queued for the next poll, exactly like a should_skip skip."""
    async def fake_skip(agent_id, db=None):
        return cb.GateVerdict(skip=False, reason=None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch, allowed=False)

    bus = _SpyBus([_msg(mentions=["ag_paused"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert probe_calls == ["ag_paused"]
    assert bus.acks == []
    assert t.batches == []


@pytest.mark.asyncio
async def test_granted_probe_runs_the_relevant_batch(monkeypatch):
    """The allowed case: the claim is taken exactly once, right before the
    batch runs."""
    async def fake_skip(agent_id, db=None):
        return cb.GateVerdict(skip=False, reason=None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch, allowed=True)

    bus = _SpyBus([_msg(mentions=["ag_paused"])])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is True

    assert probe_calls == ["ag_paused"]
    assert len(t.batches) == 1
    # The won claim rides the turn so the runtime client can settle it.
    assert t.probe_tokens == ["tok-lane"]


@pytest.mark.asyncio
async def test_held_multipart_batch_does_not_claim_the_probe(monkeypatch):
    """A long message still arriving in parts holds the lane (no ack, no
    turn) — and the hold branch sits BEFORE the claim, so the poller does
    not burn the probe on a fragment it will not run."""
    async def fake_skip(agent_id, db=None):
        return cb.GateVerdict(skip=False, reason=None)
    monkeypatch.setattr(cb, "should_skip", fake_skip)
    probe_calls = _probe_spy(monkeypatch)

    bus = _SpyBus([_msg(mentions=["ag_paused"], part_index=1, part_count=2, part_group="grp_1")])
    t = _trigger(bus)
    assert await t._process_lane("ag_paused", "ch_room") is False

    assert bus.acks == []  # held, not acked
    assert probe_calls == []  # grant untouched
    assert t.batches == []


@pytest.mark.asyncio
async def test_a_claim_the_turn_never_settled_is_handed_back(monkeypatch, db_client):
    """#394 review C1: if the batch dies before the runtime client could
    settle the probe (here: it throws while building the turn), the lane
    hands the claim back by its token — the row returns to PAUSED instead of
    sitting PROBING, refusing every entry point, until the grant expires."""
    from datetime import timedelta

    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import CbStatus, PausedReason
    from narranexus.platform.utils.timezone import utc_now

    async def _db():
        return db_client
    monkeypatch.setattr(cb, "get_db_client", _db)
    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("ag_lane", {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": "auth",
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() - timedelta(seconds=1),
    })

    bus = _SpyBus([_msg(mentions=["ag_lane"])])
    t = _trigger(bus)
    seen = []

    async def _boom(agent_id, channel_id, messages, trigger_msg, channel_owner="",
                    probe_token=None):
        seen.append((await repo.get(agent_id)).cb_status)
        raise RuntimeError("prompt build failed")
    t._handle_channel_batch = _boom

    assert await t._process_lane("ag_lane", "ch_room") is False
    assert seen == [CbStatus.PROBING.value]  # the lane really held the probe
    row = await repo.get("ag_lane")
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None
    assert row.consecutive_failure_count == 3


@pytest.mark.asyncio
async def test_invoke_runtime_hands_the_token_to_the_client(monkeypatch):
    """The lane/patrol claim reaches run_and_collect, the settlement seam."""
    from narranexus.platform.agent_runtime import client as client_mod
    from narranexus.platform.agent_runtime.run_collector import RunCollection

    got = {}

    class _Client:
        async def run_and_collect(self, **kw):
            got.update(kw)
            return RunCollection(output_text="hi")
    monkeypatch.setattr(client_mod, "get_agent_runtime_client", lambda: _Client())

    t = _trigger(_SpyBus())
    await t._invoke_runtime(
        agent_id="ag", sender_agent_id="peer", prompt="p", channel_id="ch",
        probe_token="tok-invoke",
    )
    assert got["probe_token"] == "tok-invoke"
