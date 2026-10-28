"""
@file_name: test_prompt_frame_consistency.py
@date: 2026-08-18
@description: Every timestamp in one prompt must be in one frame.

Framing the history timeline while leaving the block next to it on a raw
`[:16]` UTC slice would have been worse than leaving both alone. Before,
the two agreed (both bare UTC); after, they would describe overlapping
events — recent background activity IS the job/IM traffic the timeline
also reflects — in two different frames, one of which looks more
authoritative because it carries an offset. That is the mechanism this
whole change set exists to remove, so it must not be reintroduced twelve
lines down from the fix.

These tests assert the property (one frame per prompt) rather than the
individual renderings, so a future block that forgets the renderer fails
here rather than in production at 01:00 local time.
"""

import re

import pytest

from narranexus.platform.context_runtime.context_runtime import ContextRuntime
from narranexus.platform.schema import ContextData

TZ = "Asia/Shanghai"
_OFFSET = re.compile(r"[+-]\d{2}:\d{2}")


def test_recent_actions_render_in_the_user_frame():
    section = ContextRuntime._build_recent_actions_section(
        [{
            "timestamp": "2026-07-30T16:30:00Z",
            "working_source": "job",
            "title": "daily digest",
            "event_id": "evt_1",
        }],
        TZ,
    )
    # 16:30Z is already the 31st in Shanghai — the same off-by-one the
    # timeline had.
    assert "2026-07-31 00:30 +08:00" in section
    assert "2026-07-30 16:30" not in section


def test_recent_actions_keep_their_other_fields():
    section = ContextRuntime._build_recent_actions_section(
        [{
            "timestamp": "2026-07-30T16:30:00Z",
            "working_source": "job",
            "title": "daily digest",
            "event_id": "evt_1",
        }],
        TZ,
    )
    assert "job" in section and "daily digest" in section and "evt=evt_1" in section


def test_recent_actions_tolerate_a_missing_timestamp():
    section = ContextRuntime._build_recent_actions_section(
        [{"working_source": "job", "event_id": "evt_1"}], TZ
    )
    assert "??" in section


def test_recent_actions_default_to_utc_not_a_crash():
    """Called without a tz (any caller that has not been threaded yet), the
    block must still render — in UTC, labelled."""
    section = ContextRuntime._build_recent_actions_section(
        [{"timestamp": "2026-07-30T16:30:00Z", "working_source": "job"}]
    )
    assert "2026-07-30 16:30 +00:00" in section


@pytest.mark.asyncio
async def test_timeline_and_recent_actions_share_one_offset(db_client, monkeypatch):
    """The actual invariant, end to end: assemble a turn that carries BOTH
    blocks and assert every timestamp in it names the same offset."""
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)

    await db_client.insert("users", {
        "user_id": "u_frame2",
        "display_name": "frame_user",
        "user_type": "user",
        "timezone": TZ,
        "status": "active",
    })

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_frame2"
    runtime.user_id = "u_frame2"

    ctx = ContextData(
        agent_id="agent_frame2", user_id="u_frame2", input_content="hi"
    )
    ctx.chat_history = [{
        "role": "user",
        "content": "下周五有空吗",
        "meta_data": {
            "timestamp": "2026-07-30T16:30:00Z",
            "narrative_id": "nar_f",
            "event_id": "evt_f",
            "working_source": "chat",
        },
    }]
    ctx.extra_data = {"recent_actions": [{
        "timestamp": "2026-07-30T16:45:00Z",
        "working_source": "job",
        "title": "reminder fired",
        "event_id": "evt_job",
    }]}

    final_messages, _mcp, _dis, _expr, _deferred = await runtime.build_input_for_framework(
        messages=[], system_prompt="sys", active_instances=[], ctx_data=ctx,
    )
    whole_prompt = "\n".join(m["content"] for m in final_messages)

    offsets = set(_OFFSET.findall(whole_prompt))
    assert offsets == {"+08:00"}, f"mixed frames in one prompt: {offsets}"
    # And specifically: both blocks landed on the user's calendar day.
    assert "2026-07-31 00:30 +08:00" in whole_prompt   # timeline row
    assert "2026-07-31 00:45 +08:00" in whole_prompt   # recent activity


@pytest.mark.asyncio
async def test_no_bare_utc_slice_survives_in_the_turn_prompt(db_client, monkeypatch):
    """Guard the negative directly: a `YYYY-MM-DD HH:MM` with no offset after
    it is the shape the old slice produced."""
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)

    await db_client.insert("users", {
        "user_id": "u_frame3",
        "display_name": "frame_user3",
        "user_type": "user",
        "timezone": TZ,
        "status": "active",
    })

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_frame3"
    runtime.user_id = "u_frame3"

    ctx = ContextData(
        agent_id="agent_frame3", user_id="u_frame3", input_content="hi"
    )
    ctx.chat_history = [{
        "role": "user",
        "content": "hello",
        "meta_data": {
            "timestamp": "2026-07-30T16:30:00Z",
            "narrative_id": "nar_f3",
            "event_id": "evt_f3",
            "working_source": "chat",
        },
    }]
    ctx.extra_data = {"recent_actions": [{
        "timestamp": "2026-07-30T16:45:00Z",
        "working_source": "job",
        "title": "reminder fired",
    }]}

    final_messages, _mcp, _dis, _expr, _deferred = await runtime.build_input_for_framework(
        messages=[], system_prompt="sys", active_instances=[], ctx_data=ctx,
    )
    whole_prompt = "\n".join(m["content"] for m in final_messages)

    unframed = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?![:\d])(?! ?[+-]\d{2}:\d{2})",
                          whole_prompt)
    assert not unframed, f"timestamps with no UTC offset: {unframed}"
