"""
@file_name: test_dedup_store_agent_baseline.py
@date: 2026-10-14
@description: Per-agent historic baseline in ChannelDedupStore.

The channel-wide baseline is shared by every subscriber of the channel, so one
agent's reconnect moved the filter for all of them. A channel that knows its
subscribers' session semantics (Matrix: cursor-less sync vs cursor resume) sets
a baseline per agent; every agent without an override keeps the channel-wide
behaviour exactly as before.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.channel_dedup_store import ChannelDedupStore

BASE = 100_000_000
OLD = BASE - 60 * 60 * 1000  # one hour before the baseline


@pytest.mark.asyncio
async def test_agents_without_an_override_keep_the_channel_wide_baseline():
    store = ChannelDedupStore("matrix")
    store.update_baseline(BASE)
    decision = await store.classify("m1", OLD, agent_id="agent_a")
    assert decision["accept"] is False and decision["layer"] == "historic"
    assert decision["age_min"] == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_an_agent_override_replaces_the_channel_baseline_for_that_agent_only():
    store = ChannelDedupStore("matrix")
    store.update_baseline(BASE)
    store.set_agent_baseline("agent_a", 0)  # 0 = never historic-drop

    resumed = await store.classify("m1", OLD, agent_id="agent_a")
    other = await store.classify("m2", OLD, agent_id="agent_b")
    assert resumed["accept"] is True
    assert other["layer"] == "historic"


@pytest.mark.asyncio
async def test_an_agent_baseline_is_not_monotonic_so_a_resume_can_lift_it():
    store = ChannelDedupStore("matrix")
    store.set_agent_baseline("agent_a", BASE)
    assert (await store.classify("m1", OLD, agent_id="agent_a"))["layer"] == "historic"
    store.set_agent_baseline("agent_a", 0)
    assert (await store.classify("m2", OLD, agent_id="agent_a"))["accept"] is True


@pytest.mark.asyncio
async def test_agent_baseline_cutoff_honours_the_history_buffer():
    store = ChannelDedupStore("matrix")
    store.set_agent_baseline("agent_a", BASE)
    inside = await store.classify("m1", BASE - 4 * 60 * 1000, agent_id="agent_a")
    outside = await store.classify("m2", BASE - 6 * 60 * 1000, agent_id="agent_a")
    assert inside["accept"] is True
    assert outside["layer"] == "historic"


@pytest.mark.asyncio
async def test_clearing_an_agent_baseline_falls_back_to_the_channel_baseline():
    store = ChannelDedupStore("matrix")
    store.update_baseline(BASE)
    store.set_agent_baseline("agent_a", 0)
    store.clear_agent_baseline("agent_a")
    assert (await store.classify("m1", OLD, agent_id="agent_a"))["layer"] == "historic"
