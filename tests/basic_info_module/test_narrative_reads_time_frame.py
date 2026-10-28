"""
@file_name: test_narrative_reads_time_frame.py
@date: 2026-08-18
@description: view_narrative / view_event must speak the same time frame as
the history timeline.

The timeline tag tells the agent, in words, to call `view_event(evt_…)` for
a turn's full detail. So these two surfaces are one continuous path, and
until 2026-08-18 they disagreed the moment the timeline started rendering in
the user's frame: the timeline said 2026-07-31, `view_event` for the SAME
event said 2026-07-30T16:30:00. The model would have been walking from a
framed value to a bare one on the platform's own instruction.

Also pinned here: sorting still keys off the STORED timestamp. Sorting by
the rendered string would put "??" (unparseable) rows at the top of the
history instead of leaving them in place.
"""

import re

import pytest

from narranexus_plugins.basic_info_module._narrative_reads import (
    fetch_event_view,
    fetch_narrative_view,
    narrative_chat_history,
)

TZ = "Asia/Shanghai"


async def _seed_agent(db_client, agent_id: str, tz: str = TZ):
    await db_client.insert("users", {
        "user_id": f"owner_{agent_id}",
        "display_name": "owner",
        "user_type": "user",
        "timezone": tz,
        "status": "active",
    })
    await db_client.insert("agents", {
        "agent_id": agent_id,
        "agent_name": "t",
        "created_by": f"owner_{agent_id}",
    })


@pytest.mark.asyncio
async def test_view_event_time_is_framed_in_the_owner_timezone(db_client):
    await _seed_agent(db_client, "agent_ve")
    await db_client.insert("events", {
        "event_id": "evt_ve",
        "agent_id": "agent_ve",
        "user_id": "owner_agent_ve",
        "created_at": "2026-07-30T16:30:00Z",
        "trigger": "chat",
        "trigger_source": "chat",
    })

    out = await fetch_event_view(db_client, "agent_ve", "evt_ve")
    assert out["success"] is True
    # Same instant the timeline renders as 2026-07-31 00:30 +08:00.
    assert out["time"] == "2026-07-31 00:30 +08:00"


@pytest.mark.asyncio
async def test_view_event_falls_back_to_utc_for_an_unknown_owner(db_client):
    """No agents row → no owner → UTC, labelled. Must not raise: these
    helpers promise never to."""
    await db_client.insert("events", {
        "event_id": "evt_orphan",
        "agent_id": "agent_orphan",
        "created_at": "2026-07-30T16:30:00Z",
        "trigger": "chat",
        "trigger_source": "chat",
    })
    out = await fetch_event_view(db_client, "agent_orphan", "evt_orphan")
    assert out["success"] is True
    assert out["time"] == "2026-07-30 16:30 +00:00"


@pytest.mark.asyncio
async def test_chat_history_times_are_framed(db_client):
    await db_client.insert("instance_narrative_links", {
        "instance_id": "chat_h1",
        "narrative_id": "nar_h",
    })
    await db_client.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_h1",
        "memory": (
            '{"messages": ['
            '{"role": "user", "content": "hi", '
            '"meta_data": {"timestamp": "2026-07-30T16:30:00Z", "event_id": "e1"}}'
            ']}'
        ),
    })

    messages, _truncated = await narrative_chat_history(
        db_client, "nar_h", user_tz=TZ
    )
    assert len(messages) == 1
    assert messages[0]["time"] == "2026-07-31 00:30 +08:00"
    # The private sort key must not leak into the agent-visible payload.
    assert "_sort_ts" not in messages[0]


@pytest.mark.asyncio
async def test_chat_history_orders_by_stored_timestamp_not_rendered_text(db_client):
    """The trap: an unparseable timestamp renders as "??", which sorts before
    every real date. Ordering must therefore key off the stored value, so a
    bad row stays where it was rather than jumping to the top of the history.
    """
    await db_client.insert("instance_narrative_links", {
        "instance_id": "chat_h2",
        "narrative_id": "nar_sort",
    })
    await db_client.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_h2",
        "memory": (
            '{"messages": ['
            '{"role": "user", "content": "third", '
            '"meta_data": {"timestamp": "2026-07-30T18:00:00Z"}},'
            '{"role": "user", "content": "first", '
            '"meta_data": {"timestamp": "2026-07-30T16:00:00Z"}},'
            '{"role": "user", "content": "second", '
            '"meta_data": {"timestamp": "2026-07-30T17:00:00Z"}}'
            ']}'
        ),
    })

    messages, _ = await narrative_chat_history(db_client, "nar_sort", user_tz=TZ)
    assert [m["content"] for m in messages] == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_unparseable_timestamp_stays_in_place(db_client):
    await db_client.insert("instance_narrative_links", {
        "instance_id": "chat_h3",
        "narrative_id": "nar_bad",
    })
    await db_client.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_h3",
        "memory": (
            '{"messages": ['
            '{"role": "user", "content": "early", '
            '"meta_data": {"timestamp": "2026-07-30T16:00:00Z"}},'
            '{"role": "user", "content": "broken", '
            '"meta_data": {"timestamp": "zzz-not-a-time"}}'
            ']}'
        ),
    })

    messages, _ = await narrative_chat_history(db_client, "nar_bad", user_tz=TZ)
    contents = [m["content"] for m in messages]
    # "zzz…" sorts after the ISO string; the point is that it is NOT hoisted
    # to the front by its "??" rendering.
    assert contents.index("early") < contents.index("broken")
    assert messages[contents.index("broken")]["time"] == "??"


@pytest.mark.asyncio
async def test_every_rendered_time_carries_an_offset(db_client):
    await _seed_agent(db_client, "agent_all")
    await db_client.insert("events", {
        "event_id": "evt_all",
        "agent_id": "agent_all",
        "created_at": "2026-07-30T16:30:00Z",
        "trigger": "chat",
        "trigger_source": "chat",
    })
    out = await fetch_event_view(db_client, "agent_all", "evt_all")
    assert re.search(r"[+-]\d{2}:\d{2}$", out["time"]), out["time"]


@pytest.mark.asyncio
async def test_view_narrative_history_uses_the_owner_timezone(db_client):
    """Lock the WIRING, not just the renderer.

    `narrative_chat_history` takes `user_tz` with a `DEFAULT_TIMEZONE`
    default, so dropping the `user_tz=user_tz` argument in
    `fetch_narrative_view` fails silently: every message time becomes bare
    UTC while `view_event` and the timeline stay on the owner's zone. Same
    off-by-one as the original bug, relocated to "tool A vs tool B" — and
    invisible, because both tools still return success and both times still
    carry an offset.

    Hence the assertion is the exact +08:00 value. "carries an offset" would
    also be satisfied by the broken +00:00 output, i.e. would not test
    anything.
    """
    await _seed_agent(db_client, "agent_nv")
    await db_client.insert("narratives", {
        "narrative_id": "nar_nv",
        "agent_id": "agent_nv",
        "type": "chat",
    })
    await db_client.insert("instance_narrative_links", {
        "instance_id": "chat_nv",
        "narrative_id": "nar_nv",
    })
    await db_client.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_nv",
        "memory": (
            '{"messages": ['
            '{"role": "user", "content": "hi", '
            '"meta_data": {"timestamp": "2026-07-30T16:30:00Z", "event_id": "e1"}}'
            ']}'
        ),
    })

    out = await fetch_narrative_view(db_client, "agent_nv", "nar_nv")
    # Ownership check runs first; without this the next line KeyErrors on a
    # not-found payload instead of reporting what actually broke.
    assert out["success"] is True, out
    assert out["messages"][0]["time"] == "2026-07-31 00:30 +08:00"


@pytest.mark.asyncio
async def test_both_views_agree_on_the_zone_for_one_agent(db_client):
    """The invariant behind the previous test: view_narrative and view_event
    must never resolve different zones for the same agent."""
    await _seed_agent(db_client, "agent_both")
    await db_client.insert("narratives", {
        "narrative_id": "nar_both",
        "agent_id": "agent_both",
        "type": "chat",
    })
    await db_client.insert("instance_narrative_links", {
        "instance_id": "chat_both",
        "narrative_id": "nar_both",
    })
    await db_client.insert("instance_json_format_memory_chat", {
        "instance_id": "chat_both",
        "memory": (
            '{"messages": ['
            '{"role": "user", "content": "hi", '
            '"meta_data": {"timestamp": "2026-07-30T16:30:00Z", "event_id": "evt_both"}}'
            ']}'
        ),
    })
    await db_client.insert("events", {
        "event_id": "evt_both",
        "agent_id": "agent_both",
        "created_at": "2026-07-30T16:30:00Z",
        "trigger": "chat",
        "trigger_source": "chat",
    })

    nv = await fetch_narrative_view(db_client, "agent_both", "nar_both")
    ev = await fetch_event_view(db_client, "agent_both", "evt_both")
    assert nv["success"] is True and ev["success"] is True
    # Same instant, same event, reached by two different tools.
    assert nv["messages"][0]["time"] == ev["time"] == "2026-07-31 00:30 +08:00"
