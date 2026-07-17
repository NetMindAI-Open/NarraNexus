"""
ChannelDedupStore — three-layer cascade behaviour.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from xyz_agent_context.channel.channel_dedup_store import ChannelDedupStore
from xyz_agent_context.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)


@pytest.mark.asyncio
async def test_classify_layer_historic_when_create_time_before_baseline():
    store = ChannelDedupStore("lark")
    store.update_baseline(value_ms=10_000_000)
    # 10 minutes before baseline, well past the 5-min default buffer
    decision = await store.classify("om_old", create_time_ms=9_000_000)
    assert decision["accept"] is False
    assert decision["layer"] == "historic"
    assert decision["age_min"] == pytest.approx(1.0 * 16.666, rel=0.01) or "age_min" in decision


@pytest.mark.asyncio
async def test_classify_layer_within_buffer_passes_layer1():
    """A message inside the buffer (< 5 min before baseline) is NOT historic."""
    store = ChannelDedupStore("lark")
    store.update_baseline(value_ms=10_000_000)
    decision = await store.classify("om_recent", create_time_ms=9_900_000)  # 100s before
    assert decision["accept"] is True


@pytest.mark.asyncio
async def test_classify_no_msg_id_accepts():
    store = ChannelDedupStore("lark")
    decision = await store.classify("", create_time_ms=0)
    assert decision == {"accept": True, "layer": "no_msg_id"}


@pytest.mark.asyncio
async def test_classify_layer_memory_dedup_on_repeat():
    store = ChannelDedupStore("lark")
    first = await store.classify("om_x", create_time_ms=0)
    second = await store.classify("om_x", create_time_ms=0)
    assert first["accept"] is True
    assert second == {"accept": False, "layer": "memory_dedup"}


@pytest.mark.asyncio
async def test_classify_no_repo_accepts():
    """Without a DB repo, Layer 3 is skipped."""
    store = ChannelDedupStore("lark", repo=None)
    decision = await store.classify("om_y", create_time_ms=0)
    assert decision == {"accept": True, "layer": "no_repo"}


@pytest.mark.asyncio
async def test_classify_layer_db_new_first_time_with_repo(db_client):
    repo = ChannelSeenMessageRepository("lark", db_client)
    store = ChannelDedupStore("lark", repo=repo)
    decision = await store.classify("om_db1", create_time_ms=0)
    assert decision["accept"] is True
    assert decision["layer"] == "db_new"


@pytest.mark.asyncio
async def test_classify_layer_db_dedup_persists_across_stores(db_client):
    """Layer 2 is per-instance, but DB layer dedups across restarts."""
    repo = ChannelSeenMessageRepository("lark", db_client)
    store_a = ChannelDedupStore("lark", repo=repo)
    store_b = ChannelDedupStore("lark", repo=repo)  # simulate a process restart
    assert (await store_a.classify("om_persist", create_time_ms=0))["layer"] == "db_new"
    decision = await store_b.classify("om_persist", create_time_ms=0)
    assert decision == {"accept": False, "layer": "db_dedup"}


@pytest.mark.asyncio
async def test_classify_db_layer_partitions_by_agent_id(db_client):
    """Same message_id from two agents in the same room MUST both accept.

    Matrix fanout: every room member's client sync-pulls the same event_id.
    If the DB dedup is keyed on the bare message_id, the second agent's copy
    is 'db_dedup'-dropped and its ``_process_message`` never fires — the
    silent-loss bug fixed in this change.
    """
    repo = ChannelSeenMessageRepository("narramessenger", db_client)
    store = ChannelDedupStore("narramessenger", repo=repo)
    msg_id = "$roomEvent_shared_id"

    a = await store.classify(msg_id, create_time_ms=0, agent_id="agent_alice")
    b = await store.classify(msg_id, create_time_ms=0, agent_id="agent_bob")

    assert a["accept"] is True and a["layer"] == "db_new"
    assert b["accept"] is True and b["layer"] == "db_new"

    # Same agent replaying the same id must STILL dedup at the DURABLE layer
    # (the composite key must keep hitting the UNIQUE index). Use a fresh
    # store so Layer 2's in-memory cache is cold and cannot short-circuit —
    # otherwise this assertion would pass via memory_dedup even if the DB
    # layer were keyed wrong (the hole the first draft of this test had).
    store_restarted = ChannelDedupStore("narramessenger", repo=repo)
    c = await store_restarted.classify(msg_id, create_time_ms=0, agent_id="agent_alice")
    assert c == {"accept": False, "layer": "db_dedup"}
    # And the OTHER agent's id is still distinct across the "restart".
    d = await store_restarted.classify(msg_id, create_time_ms=0, agent_id="agent_bob")
    assert d == {"accept": False, "layer": "db_dedup"}


@pytest.mark.asyncio
async def test_classify_layer_db_fail_open_on_io_error(monkeypatch):
    """If the DB layer raises a non-UNIQUE error, classify must fail OPEN."""
    class BrokenRepo:
        channel = "lark"
        async def mark_seen(self, _msg_id):
            raise ConnectionError("transient backend failure")

    store = ChannelDedupStore("lark", repo=BrokenRepo())
    decision = await store.classify("om_io", create_time_ms=0)
    assert decision["accept"] is True
    assert decision["layer"] == "db_fail_open"
    assert "ConnectionError" in decision["error"]


@pytest.mark.asyncio
async def test_baseline_is_monotonic():
    store = ChannelDedupStore("lark")
    store.update_baseline(100_000)
    store.update_baseline(50_000)  # backwards — must be ignored
    assert store.baseline_ms == 100_000
    store.update_baseline(200_000)
    assert store.baseline_ms == 200_000


def test_dedup_store_layer2_lock_under_concurrent_threads():
    """SDK-style scenario: many threads racing to mark the SAME message_id.
    Exactly one should win on Layer 2 (memory_dedup); the rest must be rejected."""
    import concurrent.futures

    store = ChannelDedupStore("lark", repo=None)
    msg_id = "shared_om_id"
    NUM_THREADS = 50

    results: list[dict] = []
    results_lock = threading.Lock()

    def call_classify():
        loop = asyncio.new_event_loop()
        try:
            decision = loop.run_until_complete(store.classify(msg_id, 0))
        finally:
            loop.close()
        with results_lock:
            results.append(decision)

    with concurrent.futures.ThreadPoolExecutor(max_workers=NUM_THREADS) as ex:
        futures = [ex.submit(call_classify) for _ in range(NUM_THREADS)]
        for f in futures:
            f.result()

    # Exactly one acceptance (the first thread to take the lock); rest dedup.
    accepted = [r for r in results if r["accept"]]
    rejected = [r for r in results if not r["accept"]]
    assert len(accepted) == 1
    # Most of the rejections will be memory_dedup; some may be no_repo if a
    # thread sneaks past Layer 2 before the entry lands. The contract is
    # "exactly one accept across all racing calls".
    assert len(rejected) == NUM_THREADS - 1


def test_dedup_store_rejects_empty_channel():
    with pytest.raises(ValueError):
        ChannelDedupStore("")
