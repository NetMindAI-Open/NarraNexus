"""
@file_name: timezone.py
@author: NetMind.AI
@date: 2026-01-20
@description: Timezone utility module

Provides unified timezone handling functions to ensure:
- Internal storage: all times use UTC
- Display/LLM: convert to user timezone

Core functions:
- utc_now(): Get UTC time (replaces all datetime.now() calls)
- to_user_timezone(dt, tz): UTC -> user timezone
- format_for_api(dt): Format as API ISO 8601 UTC format
- format_for_llm(dt, tz): Format for LLM prompts
- is_valid_timezone(tz): Validate timezone string
"""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger


# ===== Default Timezone =====

DEFAULT_TIMEZONE = "UTC"


# ===== Core Time Functions =====

def utc_now() -> datetime:
    """
    Get the current UTC time (with timezone info)

    Used to replace all datetime.now() calls, ensuring stored times are unified as UTC

    Returns:
        A datetime object with UTC timezone info
    """
    return datetime.now(timezone.utc)


def coerce_utc(value) -> Optional[datetime]:
    """Coerce a datetime or ISO-8601 string to a tz-aware UTC datetime.

    Accepts what the DB layer hands back (a ``datetime`` on some backends, the
    ISO string SQLite stores on others) or a caller-supplied ISO-8601 instant. A
    trailing ``Z`` is honoured as the UTC designator; a naive value is assumed
    to be UTC (the storage convention). Returns None for anything unusable, so a
    caller can treat an unparseable value as "absent" rather than crash.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_datetime6_literal(value) -> Optional[str]:
    """Normalise an instant to a MySQL ``DATETIME(6)`` UTC literal.

    Returns ``YYYY-MM-DD HH:MM:SS.ffffff`` in UTC (microsecond precision), or
    None if ``value`` cannot be parsed as a datetime. A caller that must both
    WRITE a timestamp and later REVERSE-LOOK-IT-UP (e.g. the gateway-key-misuse
    idempotency anchor) routes both through this one function so the two paths
    compare identical bytes — a ``Z`` form and its offset form collapse to the
    same literal.
    """
    dt = coerce_utc(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def to_user_timezone(dt, user_tz: str = DEFAULT_TIMEZONE) -> Optional[datetime]:
    """
    Convert UTC time to user timezone

    Args:
        dt: UTC datetime object or ISO 8601 string (SQLite returns strings)
        user_tz: User timezone string (IANA format, e.g., 'Asia/Shanghai')

    Returns:
        Converted datetime object (in user timezone), or None if input is None
    """
    if dt is None:
        return None
    if not isinstance(dt, (datetime, str)):
        # A type error is not a formatting failure. The except-branch below
        # exists for a datetime that cannot be converted; letting a dict or
        # a list fall into it returned `str(dict)` — a Python repr that
        # every caller then served as a "formatted timestamp" (a whole
        # `model_dump()` was fed here once; the bulletin panel rendered
        # blank rows and only a warning line said why). Loud, at the call
        # site, with the offending type named.
        raise TypeError(
            f"to_user_timezone expects datetime | str | None, got {type(dt).__name__}"
        )

    try:
        # SQLite returns timestamps as strings — parse them first
        if isinstance(dt, str):
            cleaned = dt.rstrip("Z")
            try:
                dt = datetime.fromisoformat(cleaned)
            except ValueError:
                return None

        # If naive datetime, assume it is UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert to user timezone
        target_tz = ZoneInfo(user_tz)
        return dt.astimezone(target_tz)
    except Exception as e:
        logger.warning(f"Timezone conversion failed (to_user_timezone): {e}, returning original time")
        return dt


# ===== Formatting Functions =====

def format_for_api(dt) -> Optional[str]:
    """
    Format as ISO 8601 UTC format for API responses

    Ensures that frontend JavaScript's new Date() correctly recognizes it as UTC time

    Format: YYYY-MM-DDTHH:MM:SSZ

    Args:
        dt: datetime object or ISO 8601 string (SQLite returns strings)

    Returns:
        ISO 8601 format string (with Z suffix), e.g., "2025-01-15T14:30:00Z"
        Returns None if input is None

    Raises:
        TypeError: for anything that is not a datetime, a str or None (a
            dict, a list, a whole model dump) — never silently `str()`-ed.
    """
    if dt is None:
        return None
    if not isinstance(dt, (datetime, str)):
        # A type error is not a formatting failure. The except-branch below
        # exists for a datetime that cannot be converted; letting a dict or
        # a list fall into it returned `str(dict)` — a Python repr that
        # every caller then served as a "formatted timestamp" (a whole
        # `model_dump()` was fed here once; the bulletin panel rendered
        # blank rows and only a warning line said why). Loud, at the call
        # site, with the offending type named.
        raise TypeError(
            f"format_for_api expects datetime | str | None, got {type(dt).__name__}"
        )

    try:
        # SQLite returns timestamps as strings — parse them first
        if isinstance(dt, str):
            cleaned = dt.rstrip("Z")
            try:
                dt = datetime.fromisoformat(cleaned).replace(tzinfo=timezone.utc)
            except ValueError:
                return dt  # Already formatted or unparseable, return as-is

        # If naive datetime, assume it is UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Convert to UTC (if not already UTC)
        utc_dt = dt.astimezone(timezone.utc)

        # Return ISO 8601 format with Z suffix indicating UTC
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        logger.warning(f"Time formatting failed (format_for_api): {e}")
        return str(dt) if dt else None


def format_for_llm(dt: Optional[datetime], user_tz: str = DEFAULT_TIMEZONE) -> str:
    """
    Format as LLM-friendly prompt format

    NOT part of the agent-facing rendering layer below, despite the name.
    It emits `2026/8/8 AM 9:00 (Asia/Shanghai)` — a zone NAME but no numeric
    offset, no weekday, and a different date shape from
    `format_now_for_agent`. A prompt carrying both formats gives the model
    two differently-shaped "now"s, which by this module's own argument can
    only add doubt.

    Its remaining callers are the job execution prompt and job result
    messages. New agent-facing code should use `format_now_for_agent` /
    `format_timestamp_for_agent` instead; converting those callers is
    deliberately out of scope of the 2026-08-18 change (see the SCOPE note
    below) and tracked in the uncovered list there.

    Format: YYYY/M/D AM/PM H:MM (timezone)

    Args:
        dt: datetime object (UTC or with timezone info)
        user_tz: User timezone string

    Returns:
        Formatted time string, e.g., "2025/1/15 PM 2:30 (Asia/Shanghai)"
    """
    if dt is None:
        return "Time unknown"

    try:
        # Convert to user timezone
        local_dt = to_user_timezone(dt, user_tz)
        if local_dt is None:
            return "Time unknown"

        # Format
        year = local_dt.year
        month = local_dt.month
        day = local_dt.day
        hour = local_dt.hour
        minute = local_dt.minute

        # AM/PM
        if hour < 12:
            period = "AM"
            display_hour = hour if hour > 0 else 12
        else:
            period = "PM"
            display_hour = hour - 12 if hour > 12 else 12

        return f"{year}/{month}/{day} {period} {display_hour}:{minute:02d} ({user_tz})"
    except Exception as e:
        logger.warning(f"Time formatting failed (format_for_llm): {e}")
        return str(dt)


# ===== Timezone Validation =====

def is_valid_timezone(tz_str: str) -> bool:
    """
    Validate whether a timezone string is valid

    Args:
        tz_str: Timezone string (IANA format)

    Returns:
        Whether it is a valid timezone string
    """
    try:
        ZoneInfo(tz_str)
        return True
    except Exception:
        return False


def resolve_timezone(user_tz: Optional[str]) -> str:
    """Return a usable IANA timezone name, falling back to UTC.

    Centralised so that a missing or malformed `users.timezone` degrades the
    same way everywhere: the time stays correct (UTC) and the LABEL says
    "UTC" rather than echoing the unusable string back at the agent.
    """
    return user_tz if user_tz and is_valid_timezone(user_tz) else DEFAULT_TIMEZONE


# ===== Agent-facing Formatting =====
#
# The rule the codebase learned the hard way (see the docstrings below and
# `.mindflow/mirror/.../timezone.py.md`): an agent cannot reason about two
# timestamps unless it can see they are in the SAME frame. Emitting one value
# as user-local and another as bare UTC does not read as "two frames" to a
# language model — it reads as a contradiction it will rationalise away.
# Hence every renderer here carries an explicit UTC offset.
#
# SCOPE, stated honestly (2026-08-18). These renderers cover the TURN PROMPT
# and the narrative/event view tools reachable from it:
#   - the ground-truth "now" (BasicInfoModule's Real World Information)
#   - chat-history timeline rows and the recent-background-activity block
#   - view_narrative / view_event (`basic_info_module/_narrative_reads.py`)
#   - the date MCP tools, which parse and return in the same shapes
#
# NOT yet covered — these still render times their own way, and a prompt that
# contains one of them contains two frames:
#   - `format_for_llm` (job execution prompt: current time, created-at;
#     job result messages) — see its docstring
#   - `general_memory_module` recalled-memory stamps (bare UTC)
#   - `social_network_module` "Last contact" (bare UTC date)
#   - `awareness_module` rename records (bare UTC date)
#
# The list is here rather than in a ticket because the invariant is only
# useful if its boundary is checkable: someone adding a time field should be
# able to tell at a glance whether they are inside it. Extend the list, or
# extend the coverage — but do not quietly let the two drift.

WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


def _with_offset_colon(s: str) -> str:
    """`strftime("%z")` yields "+0800"; LLMs overwhelmingly see "+08:00"."""
    if len(s) >= 5 and s[-5] in ("+", "-"):
        return s[:-2] + ":" + s[-2:]
    return s


def format_now_for_agent(user_tz: str) -> str:
    """Render the current time in a form an agent can reason about.

    Emits ``2026-04-21 17:45:08 +08:00 (Tuesday, Asia/Shanghai)`` — the user's
    local wall clock, an explicit UTC offset, and the weekday.

    Lives here rather than in a Module because three unrelated consumers need
    the SAME bytes: BasicInfoModule's "Real World Information" ground truth,
    the date MCP tools' reference point, and the diagnostic temporal guard
    (`utils/temporal_guard.py`) that checks replies against it. A Module-owned
    copy would have made two of those importers reach into a Module (铁律 #3).

    The three properties are all load-bearing, each from a real incident:
      1. Not naive — an agent that cannot tell UTC from user-local from
         server-local will explain a mismatch away as "server relative time"
         instead of catching it.
      2. Not the server clock — a UTC backend and an Asia user disagree about
         what day it is for eight hours out of every twenty-four.
      3. Weekday labelled — "下周五" / "this Friday" cannot be resolved from a
         bare date without the model doing calendar arithmetic in its head,
         which is exactly the step that goes wrong.

    Falls back to UTC when `user_tz` is unknown or invalid.
    """
    now_utc = utc_now()
    effective_tz = resolve_timezone(user_tz)
    local = to_user_timezone(now_utc, effective_tz)
    if local is None:
        local = now_utc

    weekday = WEEKDAY_NAMES[local.weekday()]
    base = _with_offset_colon(local.strftime("%Y-%m-%d %H:%M:%S %z"))
    return f"{base} ({weekday}, {effective_tz})"


def format_timestamp_for_agent(dt, user_tz: str) -> str:
    """Render a STORED timestamp for agent consumption, compact but framed.

    Emits ``2026-07-30 23:00 +08:00``: minute precision (these go on every
    history row, so seconds are noise) but the offset stays, because dropping
    it is precisely how a UTC-stored history came to be read as if it were
    the user's local wall clock.

    Accepts whatever the storage layer hands back — `datetime` or the ISO
    string SQLite returns. Returns "??" for anything unusable, matching the
    caller's previous behaviour for a missing timestamp.
    """
    if not dt:
        return "??"
    effective_tz = resolve_timezone(user_tz)
    local = to_user_timezone(dt, effective_tz)
    if local is None:
        return "??"
    if local.tzinfo is None:
        local = local.replace(tzinfo=timezone.utc)
    return _with_offset_colon(local.strftime("%Y-%m-%d %H:%M %z"))


