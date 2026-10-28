"""
@file_name: test_agent_time_format.py
@date: 2026-04-21
@description: Lock the contract of the agent-facing time renderers.

Symptom this guards against: an agent, asked for "today's meetings",
returned three meetings — but two were actually yesterday. The agent saw
times like 22:07 while it was currently 17:45 and rationalized the
mismatch as "server relative time" instead of catching it. Root cause on
the prompt side: current_time was `datetime.now().isoformat()` → naive,
no timezone, no weekday, potentially in server's local clock instead of
the user's. The agent had no reliable anchor to sanity-check against.

New contract:
  - Resolved in the user's timezone (IANA string)
  - Explicit UTC offset included (e.g. "+08:00")
  - Weekday label for extra human anchor
  - Invalid/missing tz falls back to UTC with a clean "UTC" label
    (never echoes the invalid tz string back as a label)

2026-08-18: the renderer moved out of BasicInfoModule into
`utils/timezone` (three consumers now need identical bytes), and
`format_timestamp_for_agent` joined it — the STORED-timestamp renderer
whose missing offset let a UTC-stored chat history be read as if it
were the user's local wall clock.
"""

import re

from narranexus.platform.utils.timezone import (
    format_now_for_agent,
    format_timestamp_for_agent,
)


_BASE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2} "
    r"\((Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [^)]+\)$"
)


def test_format_has_offset_weekday_and_tz_label():
    s = format_now_for_agent("Asia/Shanghai")
    assert _BASE_PATTERN.match(s), f"unexpected shape: {s!r}"
    assert "+08:00" in s
    assert "Asia/Shanghai" in s


def test_utc_timezone_shows_plus_00_offset():
    s = format_now_for_agent("UTC")
    assert _BASE_PATTERN.match(s)
    assert "+00:00" in s
    assert "UTC" in s


def test_empty_timezone_falls_back_to_utc():
    s = format_now_for_agent("")
    assert _BASE_PATTERN.match(s)
    assert "+00:00" in s
    # fallback must label as UTC, not echo the empty string
    assert s.rstrip(")").endswith("UTC")


def test_invalid_timezone_falls_back_to_utc_cleanly():
    """Guard against echoing the invalid tz in the label — observed before
    the `is_valid_timezone` check was added."""
    s = format_now_for_agent("Not/A/Zone")
    assert _BASE_PATTERN.match(s)
    assert "Not/A/Zone" not in s
    assert "UTC" in s
    assert "+00:00" in s


def test_weekday_matches_date():
    """Weekday label must be consistent with the calendar date emitted."""
    from datetime import date
    s = format_now_for_agent("UTC")
    # Extract "YYYY-MM-DD" + weekday
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s.*\((\w+),", s)
    assert m, f"unexpected format: {s!r}"
    date_str, weekday_str = m.group(1), m.group(2)
    expected = ["Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"][date.fromisoformat(date_str).weekday()]
    assert weekday_str == expected


def test_format_is_stable_within_one_second():
    """Two successive calls should agree on the minute (rare race at the
    second boundary is tolerated)."""
    a = format_now_for_agent("UTC")
    b = format_now_for_agent("UTC")
    # Compare the first 16 chars = "YYYY-MM-DD HH:MM"
    assert a[:16] == b[:16]


# ---------------------------------------------------------------------------
# format_timestamp_for_agent — the STORED-timestamp renderer.
#
# The bug it exists for: chat history rows are stored as `utc_now().isoformat()`
# and used to be rendered by slicing the raw string (`ts[:16]`), which silently
# dropped the frame. An agent then compared a bare UTC "2026-07-30 15:00"
# against a user-local ground truth of "2026-07-30 23:00 +08:00" with nothing
# telling it the two were eight hours apart — an off-by-one-day whenever the
# user talks between local midnight and 08:00.
# ---------------------------------------------------------------------------

_TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} [+-]\d{2}:\d{2}$")


def test_stored_timestamp_is_converted_to_user_timezone():
    """A UTC-stored value must come back as the user's wall clock."""
    s = format_timestamp_for_agent("2026-07-30T15:00:00Z", "Asia/Shanghai")
    assert s == "2026-07-30 23:00 +08:00"


def test_stored_timestamp_crossing_midnight_reports_the_users_date():
    """The off-by-one-day case, stated explicitly.

    16:30 UTC on 2026-07-30 is already 2026-07-31 in Shanghai. The old
    string-slice rendered "2026-07-30 16:30" and the agent read the DATE as
    the user's — which is how "下周五" got anchored to the wrong day.
    """
    s = format_timestamp_for_agent("2026-07-30T16:30:00Z", "Asia/Shanghai")
    assert s.startswith("2026-07-31 00:30")
    assert s.endswith("+08:00")


def test_stored_timestamp_accepts_datetime_objects():
    from datetime import datetime, timezone as _tz

    dt = datetime(2026, 7, 30, 15, 0, tzinfo=_tz.utc)
    assert format_timestamp_for_agent(dt, "Asia/Shanghai") == "2026-07-30 23:00 +08:00"


def test_naive_stored_timestamp_is_treated_as_utc():
    """SQLite hands back naive ISO strings; storage policy says they are UTC."""
    s = format_timestamp_for_agent("2026-07-30T15:00:00", "Asia/Shanghai")
    assert s == "2026-07-30 23:00 +08:00"


def test_stored_timestamp_always_carries_an_offset():
    for tz in ("Asia/Shanghai", "UTC", "America/New_York", "Not/A/Zone", ""):
        s = format_timestamp_for_agent("2026-07-30T15:00:00Z", tz)
        assert _TS_PATTERN.match(s), f"{tz}: unexpected shape {s!r}"


def test_missing_or_unparseable_timestamp_degrades_to_placeholder():
    """Matches the caller's pre-existing behaviour for a missing timestamp —
    a rendering gap must never take the turn down."""
    assert format_timestamp_for_agent("", "Asia/Shanghai") == "??"
    assert format_timestamp_for_agent(None, "Asia/Shanghai") == "??"
    assert format_timestamp_for_agent("not-a-timestamp", "Asia/Shanghai") == "??"
