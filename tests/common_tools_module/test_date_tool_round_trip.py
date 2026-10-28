"""
@file_name: test_date_tool_round_trip.py
@date: 2026-08-18
@description: The renderers and the parser must agree — cross-component.

`test_agent_time_format.py` tests the renderers. `test_date_tool.py` tests
the tools with hand-written ISO inputs. Both were green while the one
combination that actually occurs in production — the agent copying a
timestamp it can SEE in its prompt and passing it to a date tool — was never
exercised.

That combination is not hypothetical: it is what the prompts instruct.
`COMMON_TOOLS_INSTRUCTIONS` says "pass that message's date as `reference` —
the timeline lines carry it", and the temporal block says to confirm with
`compare_dates`. The most likely token for a model to pass is one it can
literally read off the prompt.

And the failure mode is silent all the way down: the tool returns a
structured `bad_reference`, the model falls back to doing the arithmetic
itself (the step these tools exist to remove), `temporal_guard` does not
record it because a rejected argument is not a date claim, and `service_audit`
therefore reads as "no problems". The only visible symptom is a user being
told the wrong day — again.

So these tests feed each renderer's real output straight into the parser and
the tools. If a renderer's shape ever changes, this file fails rather than
the production path degrading quietly.
"""

from datetime import date, datetime, timezone as dt_timezone

import pytest
from mcp.server.fastmcp import FastMCP

from narranexus.platform.context_runtime.context_runtime import ContextRuntime
from narranexus_plugins.common_tools_module._common_tools_impl import date_tool
from narranexus_plugins.common_tools_module._common_tools_impl.date_tool import (
    _parse_date,
)
from narranexus.platform.utils.timezone import (
    format_now_for_agent,
    format_timestamp_for_agent,
)

TZ = "Asia/Shanghai"


@pytest.fixture
def tools():
    mcp = FastMCP("test_date_round_trip")
    date_tool.register(mcp)
    return {t.name: t.fn for t in mcp._tool_manager.list_tools()}


@pytest.fixture(autouse=True)
def _fixed_clock_and_tz(monkeypatch):
    """Freeze BOTH clocks — the renderers' and the tools'.

    `format_now_for_agent` reads `utils.timezone.utc_now`; `date_tool` holds
    its own imported reference. Patching only one makes the renderer and the
    tool disagree about today, which is precisely the class of bug this file
    exists to catch — so the fixture must not introduce it itself.
    """
    import narranexus.platform.utils.timezone as tz_mod

    frozen = datetime(2026, 8, 8, 1, 0, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(tz_mod, "utc_now", lambda: frozen)
    monkeypatch.setattr(date_tool, "utc_now", lambda: frozen)

    async def _tz(user_id: str = ""):
        return TZ

    monkeypatch.setattr(date_tool, "_caller_timezone", _tz)


# ---------------------------------------------------------------------------
# Renderer output -> parser
# ---------------------------------------------------------------------------

def test_timeline_timestamp_round_trips():
    """What the agent reads on a history line must parse back to that day —
    the USER's day, not the UTC one."""
    rendered = format_timestamp_for_agent("2026-07-30T16:30:00Z", TZ)
    assert rendered == "2026-07-31 00:30 +08:00"
    assert _parse_date(rendered, TZ) == date(2026, 7, 31)


def test_ground_truth_now_round_trips():
    """`format_now_for_agent` carries a ` (Weekday, Zone)` tail that plain
    `fromisoformat` rejects. The agent sees this string as "now" and will
    hand it to compare_dates."""
    rendered = format_now_for_agent(TZ)
    parsed = _parse_date(rendered, TZ)
    assert parsed is not None, f"ground-truth now did not parse: {rendered!r}"
    assert parsed.isoformat() == rendered[:10]


def test_timeline_tag_value_round_trips():
    """One level up: the exact substring the agent copies out of a timeline
    tag, taken from the tag builder rather than reconstructed by hand."""
    tag = ContextRuntime._format_timeline_tag(
        {"timestamp": "2026-07-30T16:30:00Z", "narrative_id": "n", "event_id": "e"},
        TZ,
    )
    stamp = tag[1:].split(" · ")[0]
    assert _parse_date(stamp, TZ) == date(2026, 7, 31)


@pytest.mark.parametrize("tz", ["Asia/Shanghai", "UTC", "America/New_York"])
def test_round_trip_holds_across_timezones(tz):
    rendered = format_timestamp_for_agent("2026-07-30T16:30:00Z", tz)
    assert _parse_date(rendered, tz) is not None
    assert _parse_date(format_now_for_agent(tz), tz) is not None


# ---------------------------------------------------------------------------
# The strip must not become a blunt "take the first 10 chars"
# ---------------------------------------------------------------------------

def test_offset_bearing_timestamps_still_convert_before_taking_the_date():
    """The tempting fix — slice off `YYYY-MM-DD` — would reintroduce the very
    off-by-one this change set removes: 16:30Z is already the 31st in
    Shanghai. Locked so nobody "simplifies" the strip into a slice."""
    assert _parse_date("2026-07-30T16:30:00Z", TZ) == date(2026, 7, 31)
    assert _parse_date("2026-07-30T16:30:00+00:00", TZ) == date(2026, 7, 31)


def test_annotation_strip_does_not_swallow_real_content():
    assert _parse_date("2026-07-30", TZ) == date(2026, 7, 30)
    assert _parse_date("(nonsense)", TZ) is None
    assert _parse_date("", TZ) is None


# ---------------------------------------------------------------------------
# Renderer output -> the tools themselves
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_relative_date_accepts_a_rendered_timeline_stamp(tools):
    """The full documented flow: resolving what "下周五" meant in a message
    the agent can see was sent on 2026-07-31."""
    rendered = format_timestamp_for_agent("2026-07-30T16:30:00Z", TZ)
    r = await tools["resolve_relative_date"](
        unit="week", offset=1, weekday="friday", reference=rendered
    )
    assert "error" not in r, r
    assert r["reference"] == "2026-07-31"
    assert r["date"] == "2026-08-07"
    assert r["is_past"] is True


@pytest.mark.asyncio
async def test_compare_dates_accepts_the_ground_truth_now_string(tools):
    """A model asked to check "is today the day?" will reach for the value
    labelled as now. It must not get `bad_date` back."""
    r = await tools["compare_dates"](
        date_a="2026-08-07", date_b=format_now_for_agent(TZ)
    )
    assert "error" not in r, r
    assert r["compared_to"] == "2026-08-08"
    assert r["order"] == "before"
    assert r["is_past"] is True


@pytest.mark.asyncio
async def test_compare_dates_accepts_a_rendered_timeline_stamp(tools):
    rendered = format_timestamp_for_agent("2026-08-08T16:30:00Z", TZ)
    r = await tools["compare_dates"](date_a=rendered)
    assert "error" not in r, r
    assert r["date"] == "2026-08-09"
    assert r["is_future"] is True
