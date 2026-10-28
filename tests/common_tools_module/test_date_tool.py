"""
@file_name: test_date_tool.py
@date: 2026-08-18
@description: Lock the calendar arithmetic behind resolve_relative_date /
compare_dates.

These tools exist because agents were doing this arithmetic by reasoning and
getting it wrong silently. A test suite that only checked the happy path
would leave exactly the cases that bite: week boundaries, month-end
clamping, the timezone in which "today" is decided, and the past/future
comparison that the reported incident actually failed.

The tools are registered on a FastMCP server, so the tests pull the
registered handlers off a real server instance rather than reaching into
module-private functions — that way a registration mistake (wrong name,
tool not registered at all) fails here too.
"""

from datetime import date, datetime, timezone as dt_timezone

import pytest
from mcp.server.fastmcp import FastMCP

from narranexus_plugins.common_tools_module._common_tools_impl import date_tool


@pytest.fixture
def tools():
    """The registered handlers, keyed by tool name."""
    mcp = FastMCP("test_date_tools")
    date_tool.register(mcp)
    registered = {}

    # FastMCP keeps the callables in its tool manager; grab them by name so
    # a rename or a missing register() call surfaces as a KeyError here.
    for tool in mcp._tool_manager.list_tools():
        registered[tool.name] = tool.fn
    return registered


@pytest.fixture(autouse=True)
def _fixed_clock_and_tz(monkeypatch):
    """Freeze "now" and pin the timezone.

    2026-08-08 01:00 UTC is 09:00 in Shanghai — the same instant, two dates
    apart from 2026-08-07 17:00 UTC. Pinning both halves is what makes the
    timezone assertions below mean anything.
    """
    monkeypatch.setattr(
        date_tool, "utc_now",
        lambda: datetime(2026, 8, 8, 1, 0, tzinfo=dt_timezone.utc),
    )

    async def _tz(user_id: str = ""):
        return "Asia/Shanghai"

    monkeypatch.setattr(date_tool, "_caller_timezone", _tz)


# ---------------------------------------------------------------------------
# resolve_relative_date
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tomorrow(tools):
    r = await tools["resolve_relative_date"](unit="day", offset=1)
    assert r["date"] == "2026-08-09"
    assert r["weekday"] == "Sunday"
    assert r["days_from_today"] == 1
    assert r["is_future"] is True


@pytest.mark.asyncio
async def test_day_after_tomorrow_and_yesterday(tools):
    assert (await tools["resolve_relative_date"](unit="day", offset=2))["date"] == "2026-08-10"
    y = await tools["resolve_relative_date"](unit="day", offset=-1)
    assert y["date"] == "2026-08-07"
    assert y["is_past"] is True


@pytest.mark.asyncio
async def test_next_friday_is_the_friday_of_next_week(tools):
    """The headline case. Today is Saturday 2026-08-08, so "this week" is the
    Mon 08-03 … Sun 08-09 week and next week's Friday is 08-14 — NOT
    "the coming Friday plus seven"."""
    r = await tools["resolve_relative_date"](unit="week", offset=1, weekday="friday")
    assert r["date"] == "2026-08-14"
    assert r["weekday"] == "Friday"
    assert r["week_start"] == "2026-08-10"
    assert r["week_end"] == "2026-08-16"


@pytest.mark.asyncio
async def test_this_week_weekday_can_be_in_the_past(tools):
    """Today is Saturday; "this Friday" (这周五) is yesterday, and the tool
    must say so rather than rolling forward to next week."""
    r = await tools["resolve_relative_date"](unit="week", offset=0, weekday="friday")
    assert r["date"] == "2026-08-07"
    assert r["is_past"] is True
    assert r["days_from_today"] == -1


@pytest.mark.asyncio
async def test_weekday_snap_is_applied_after_the_offset(tools):
    """Guard against the off-by-a-week variant: offset first, THEN snap into
    that week. Both directions."""
    last_monday = await tools["resolve_relative_date"](
        unit="week", offset=-1, weekday="monday"
    )
    assert last_monday["date"] == "2026-07-27"
    assert last_monday["weekday"] == "Monday"


@pytest.mark.asyncio
async def test_chinese_weekday_names_accepted(tools):
    """The model is as likely to echo the user's phrasing as to translate it;
    rejecting 周五 would push it back into guessing the date itself."""
    a = await tools["resolve_relative_date"](unit="week", offset=1, weekday="周五")
    b = await tools["resolve_relative_date"](unit="week", offset=1, weekday="friday")
    assert a["date"] == b["date"] == "2026-08-14"
    assert (await tools["resolve_relative_date"](
        unit="week", offset=0, weekday="星期日"))["date"] == "2026-08-09"


@pytest.mark.asyncio
async def test_week_offset_without_weekday_keeps_the_same_day_of_week(tools):
    r = await tools["resolve_relative_date"](unit="week", offset=3)
    assert r["date"] == "2026-08-29"
    assert r["weekday"] == "Saturday"


@pytest.mark.asyncio
async def test_month_offset_clamps_to_a_shorter_month(tools):
    """Jan 31 + 1 month is Feb 28 — clamping, not an error and not Mar 3."""
    r = await tools["resolve_relative_date"](
        unit="month", offset=1, reference="2026-01-31"
    )
    assert r["date"] == "2026-02-28"


@pytest.mark.asyncio
async def test_month_offset_clamps_into_a_leap_february(tools):
    r = await tools["resolve_relative_date"](
        unit="month", offset=1, reference="2028-01-31"
    )
    assert r["date"] == "2028-02-29"


@pytest.mark.asyncio
async def test_year_offset(tools):
    r = await tools["resolve_relative_date"](unit="year", offset=1)
    assert r["date"] == "2027-08-08"


@pytest.mark.asyncio
async def test_reference_anchors_to_another_day(tools):
    """Resolving what "下周五" meant in a message sent on 2026-07-30: the
    reference week is Mon 07-27 … Sun 08-02, so next week's Friday is 08-07.
    This is the actual reported case, reconstructed."""
    r = await tools["resolve_relative_date"](
        unit="week", offset=1, weekday="friday", reference="2026-07-30"
    )
    assert r["date"] == "2026-08-07"
    assert r["reference"] == "2026-07-30"
    assert r["reference_weekday"] == "Thursday"
    # ...and relative to today (2026-08-08) it has already passed. This is
    # the assertion the agent got wrong in production.
    assert r["is_past"] is True
    assert r["is_today"] is False
    assert r["days_from_today"] == -1


@pytest.mark.asyncio
async def test_reference_accepts_a_full_iso_timestamp(tools):
    """The agent usually holds a stored timestamp, not a bare date."""
    r = await tools["resolve_relative_date"](
        unit="day", offset=1, reference="2026-07-30T16:30:00Z"
    )
    # 16:30 UTC is already the 31st in Shanghai, so "tomorrow" is Aug 1.
    assert r["reference"] == "2026-07-31"
    assert r["date"] == "2026-08-01"


@pytest.mark.asyncio
async def test_today_is_decided_in_the_users_timezone(tools, monkeypatch):
    """01:00 UTC on the 8th is 09:00 on the 8th in Shanghai but still 21:00
    on the 7th in New York. "Today" must follow the user."""
    async def _ny(user_id: str = ""):
        return "America/New_York"

    monkeypatch.setattr(date_tool, "_caller_timezone", _ny)
    r = await tools["resolve_relative_date"](unit="day", offset=0)
    assert r["date"] == "2026-08-07"
    assert r["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_bad_inputs_return_structured_errors_not_exceptions(tools):
    """A tool that raises teaches the model to go back to guessing."""
    bad_unit = await tools["resolve_relative_date"](unit="fortnight", offset=1)
    assert bad_unit["code"] == "bad_unit"

    bad_wd = await tools["resolve_relative_date"](
        unit="week", offset=1, weekday="funday"
    )
    assert bad_wd["code"] == "bad_weekday"

    bad_ref = await tools["resolve_relative_date"](
        unit="day", offset=1, reference="last tuesday"
    )
    assert bad_ref["code"] == "bad_reference"


# ---------------------------------------------------------------------------
# compare_dates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compare_reproduces_the_reported_incident(tools):
    """The event was on 2026-08-07; the agent said on 2026-08-08 that today
    was the event day. One call answers it."""
    r = await tools["compare_dates"](date_a="2026-08-07")
    assert r["is_today"] is False
    assert r["is_past"] is True
    assert r["days_from_today"] == -1
    assert r["today"] == "2026-08-08"


@pytest.mark.asyncio
async def test_compare_defaults_second_date_to_today(tools):
    r = await tools["compare_dates"](date_a="2026-08-08")
    assert r["is_today"] is True
    assert r["compared_to"] == "2026-08-08"
    assert r["compared_to_is_today"] is True
    assert r["order"] == "same_day"


@pytest.mark.asyncio
async def test_compare_two_explicit_dates(tools):
    r = await tools["compare_dates"](date_a="2026-08-20", date_b="2026-08-10")
    assert r["days_between"] == 10
    assert r["order"] == "after"


@pytest.mark.asyncio
async def test_explicit_date_b_does_not_hijack_the_today_reading(tools):
    """`is_past` always means "against today"; `order` always means "against
    date_b". Conflating them is how a comparison silently answers a question
    nobody asked."""
    r = await tools["compare_dates"](date_a="2026-08-05", date_b="2026-08-01")
    assert r["order"] == "after"          # after date_b
    assert r["is_past"] is True           # but still before today
    assert r["days_from_today"] == -3
    assert r["days_between"] == 4
    assert r["compared_to_is_today"] is False


@pytest.mark.asyncio
async def test_compare_uses_calendar_days_in_the_users_timezone(tools):
    """A late-night timestamp must not read as the next day the way a raw
    UTC comparison would. 2026-08-08T16:30Z is Aug 9 in Shanghai."""
    r = await tools["compare_dates"](date_a="2026-08-08T16:30:00Z")
    assert r["date"] == "2026-08-09"
    assert r["is_future"] is True


@pytest.mark.asyncio
async def test_compare_bad_input_returns_structured_error(tools):
    r = await tools["compare_dates"](date_a="sometime next week")
    assert r["code"] == "bad_date"
    r2 = await tools["compare_dates"](date_a="2026-08-08", date_b="whenever")
    assert r2["code"] == "bad_date"


@pytest.mark.asyncio
async def test_both_tools_report_the_timezone_they_used(tools):
    """The agent has to be able to quote the frame; an unframed date is how
    this class of bug hides."""
    a = await tools["resolve_relative_date"](unit="day", offset=0)
    b = await tools["compare_dates"](date_a="2026-08-08")
    assert a["timezone"] == b["timezone"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_weekday_labels_agree_with_the_dates(tools):
    """Cross-check the label against the calendar — a weekday name that
    drifts from its date is worse than no label."""
    for offset in range(-10, 11):
        r = await tools["resolve_relative_date"](unit="day", offset=offset)
        expected = ["Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday"][
            date.fromisoformat(r["date"]).weekday()
        ]
        assert r["weekday"] == expected
