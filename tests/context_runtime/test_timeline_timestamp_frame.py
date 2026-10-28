"""
@file_name: test_timeline_timestamp_frame.py
@date: 2026-08-18
@description: Lock the timezone frame of chat-history timeline tags.

The defect: `meta_data.timestamp` is written by every producer as
`utc_now().isoformat()`, and `_format_timeline_tag` rendered it with a raw
`ts[:16]` string slice. The frame was therefore dropped on the floor, while
the SAME prompt carried "Real World Information" as the user's local wall
clock WITH a `+08:00`-style offset.

For a user at +08:00 that means every message sent between local 00:00 and
08:00 was tagged with the previous calendar day. An agent asked to resolve
"下周五" against a message it believes was sent on Thursday (it was Friday)
lands a week's plan on the wrong date, and a date that has already passed
still reads as upcoming. That is the reported failure mode.

Contract locked here:
  1. Timeline timestamps are converted to the user's timezone.
  2. They always carry an explicit UTC offset.
  3. That offset matches the one on the ground-truth "now", so the two are
     directly comparable.
  4. A missing/garbage timestamp degrades to "??" and never raises.
"""

import re

import pytest

from narranexus.platform.context_runtime.context_runtime import ContextRuntime
from narranexus.platform.utils.timezone import format_now_for_agent


def _tag(ts, tz="Asia/Shanghai"):
    return ContextRuntime._format_timeline_tag(
        {
            "timestamp": ts,
            "narrative_id": "nar_abc",
            "narrative_alias": "Trip planning",
            "event_id": "evt_123",
        },
        tz,
    )


def test_tag_renders_timestamp_in_user_timezone():
    assert "2026-07-30 23:00 +08:00" in _tag("2026-07-30T15:00:00Z")


def test_tag_reports_the_users_calendar_date_across_midnight():
    """The off-by-one-day case that motivated the fix.

    16:30 UTC on the 30th is 00:30 on the 31st in Shanghai. The old slice
    rendered "2026-07-30 16:30" and the model read the date as the user's.
    """
    tag = _tag("2026-07-30T16:30:00Z")
    assert "2026-07-31 00:30 +08:00" in tag
    assert "2026-07-30" not in tag


def test_tag_keeps_the_other_anchors():
    """Timezone work must not disturb the narrative/event anchors the agent
    needs for switch_narrative / view_event."""
    tag = _tag("2026-07-30T15:00:00Z")
    assert "Trip planning" in tag
    assert "nar=nar_abc" in tag
    assert "evt=evt_123" in tag


def test_tag_offset_matches_the_ground_truth_now_offset():
    """The whole point: one frame, comparable without conversion."""
    tz = "Asia/Shanghai"
    now_offset = re.search(r"([+-]\d{2}:\d{2})", format_now_for_agent(tz)).group(1)
    assert now_offset in _tag("2026-07-30T15:00:00Z", tz)


@pytest.mark.parametrize("bad", ["", None, "not-a-timestamp"])
def test_unusable_timestamp_degrades_to_placeholder(bad):
    """Preserves the pre-existing behaviour — a rendering gap never takes
    the turn down."""
    assert "??" in _tag(bad)


def test_unknown_timezone_falls_back_to_utc_without_raising():
    tag = _tag("2026-07-30T15:00:00Z", "Not/A/Zone")
    assert "2026-07-30 15:00 +00:00" in tag


@pytest.mark.asyncio
async def test_history_rows_carry_the_user_offset_end_to_end(db_client):
    """Through build_input_for_framework, not just the helper: a real row
    with a UTC-stored timestamp must reach the model in the user's frame."""
    from narranexus.platform.schema import ContextData

    await db_client.insert("users", {
        "user_id": "u_frame",
        "display_name": "frame_user",
        "user_type": "user",
        "timezone": "Asia/Shanghai",
        "status": "active",
    })

    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.db = db_client
    runtime.agent_id = "agent_frame"
    runtime.user_id = "u_frame"

    ctx = ContextData(agent_id="agent_frame", user_id="u_frame", input_content="hi")
    ctx.chat_history = [{
        "role": "user",
        "content": "下周五有空吗",
        "meta_data": {
            "timestamp": "2026-07-30T16:30:00Z",
            "narrative_id": "nar_frame",
            "event_id": "evt_frame",
            "working_source": "chat",
        },
    }]

    final_messages, _mcp, _dis, _expr, _deferred = await runtime.build_input_for_framework(
        messages=[], system_prompt="sys", active_instances=[], ctx_data=ctx,
    )

    row = next(m for m in final_messages if "下周五有空吗" in m["content"])
    assert "2026-07-31 00:30 +08:00" in row["content"]
