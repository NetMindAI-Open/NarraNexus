"""
@file_name: test_content_fingerprint_dedup.py
@author: Bin Liang
@date: 2026-07-03
@description: Content-fingerprint dedup window — the X1 double-reply guard.

NarraMessenger re-dispatches a message under a NEW invocation_id when its
15-minute server deadline expires while our worker (30-min timeout) is still
processing. All three dedup layers key on message_id, so the re-dispatch
passes and the agent runs (and replies) twice. The fix: an opt-in fourth
layer that fingerprints (chat, sender, content) within a short window —
policy lives in the trigger (what identifies "the same message" and how
long the platform may re-dispatch), mechanism in ChannelDedupStore.
"""

import time

import pytest

from xyz_agent_context.channel.channel_dedup_store import ChannelDedupStore
from xyz_agent_context.module.narramessenger_module.narramessenger_trigger import (
    NarramessengerTrigger,
)


@pytest.mark.asyncio
async def test_same_fingerprint_new_id_rejected_within_window():
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=1200)
    first = await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="fp1")
    assert first["accept"] is True
    redispatch = await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="fp1")
    assert redispatch["accept"] is False
    assert redispatch["layer"] == "content_dedup"


@pytest.mark.asyncio
async def test_fingerprint_expires_after_window(monkeypatch):
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=60)
    await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="fp1")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 61)
    later = await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="fp1")
    assert later["accept"] is True


@pytest.mark.asyncio
async def test_window_slides_on_hit(monkeypatch):
    """A re-dispatch HIT must refresh the fingerprint timestamp (sliding
    window). With a fixed window shorter than the 30-min worker timeout, a
    second re-dispatch at t~30 would land past the original stamp and be
    accepted — exactly X1 again. As long as re-dispatch intervals stay
    below the window, a turn of ANY length stays covered."""
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=60)
    real_time = time.time
    base = real_time()
    now = {"t": base}
    monkeypatch.setattr(time, "time", lambda: now["t"])

    assert (await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="fp1"))["accept"] is True
    now["t"] = base + 45  # first re-dispatch inside the window -> dropped
    assert (await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="fp1"))["accept"] is False
    now["t"] = base + 90  # 90 > 60 past the ORIGINAL stamp, but only 45 past the refreshed one
    hit = await store.classify("inv_C", 0, agent_id="ag", content_fingerprint="fp1")
    assert hit["accept"] is False, "hit must slide the window, not expire from the first stamp"
    now["t"] = base + 200  # quiet past a full window since the LAST sighting -> expires
    assert (await store.classify("inv_D", 0, agent_id="ag", content_fingerprint="fp1"))["accept"] is True


@pytest.mark.asyncio
async def test_disabled_by_default_and_when_window_zero():
    store = ChannelDedupStore("testchan", repo=None)
    a = await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="fp1")
    b = await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="fp1")
    assert a["accept"] is True and b["accept"] is True


@pytest.mark.asyncio
async def test_different_fingerprints_both_accepted():
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=1200)
    a = await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="fp1")
    b = await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="fp2")
    assert a["accept"] is True and b["accept"] is True


@pytest.mark.asyncio
async def test_empty_fingerprint_never_dedupes():
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=1200)
    a = await store.classify("inv_A", 0, agent_id="ag", content_fingerprint="")
    b = await store.classify("inv_B", 0, agent_id="ag", content_fingerprint="")
    assert a["accept"] is True and b["accept"] is True


@pytest.mark.asyncio
async def test_fingerprint_partitioned_by_agent():
    """Two binds in one process must not swallow each other's messages."""
    store = ChannelDedupStore("testchan", repo=None, content_window_seconds=1200)
    a = await store.classify("inv_A", 0, agent_id="ag1", content_fingerprint="fp1")
    b = await store.classify("inv_B", 0, agent_id="ag2", content_fingerprint="fp1")
    assert a["accept"] is True and b["accept"] is True


def test_narramessenger_window_covers_platform_deadline():
    """The platform re-dispatches after its 15-min invocation deadline; the
    window must cover that with margin or the guard is decorative."""
    assert NarramessengerTrigger.CONTENT_DEDUP_WINDOW_SECONDS >= 16 * 60
