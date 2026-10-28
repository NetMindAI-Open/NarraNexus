"""
@file_name: date_tool.py
@author: NarraNexus
@date: 2026-08-18
@description: Register `resolve_relative_date` and `compare_dates` on the
common_tools_module FastMCP server — deterministic calendar arithmetic, so
the model never has to do it in its head.

Why these exist
---------------
Every agent handles time, and every agent was doing the arithmetic by
reasoning. Three failure modes were observed, all of them silent:

  1. Relative expressions resolve to the wrong date. A user said "下周五" on
     a Thursday; the agent recorded the correct timestamp but named the
     wrong day when it came back to it.
  2. A stored absolute date is compared against "now" incorrectly, so a date
     that has already passed is still described as upcoming.
  3. Both get worse across a timezone boundary, where "today" itself differs
     by one depending on which clock you read.

Getting a date wrong is not a cosmetic error — a user who is told the wrong
day stops trusting every other date the agent gives them.

The division of labour
----------------------
These tools deliberately do NOT parse natural language. Language
understanding is the one part of this the model is genuinely good at, and a
parser would be a second, worse interpreter of it — locale-dependent,
silently wrong on the phrasings it did not anticipate, and impossible to
test against real usage.

So the model decomposes the phrase ("下周五" → the Friday of the week after
this one) and the tool does the arithmetic. Each half does what it is
reliable at, and the tool's contract is total: for any (unit, offset,
weekday, reference) it returns exactly one date, with no ambiguity left.

Ambiguity is surfaced, not resolved
-----------------------------------
"next Friday" means different things to different people (and 下周五 does
not mean the same as "this coming Friday"). The tool cannot know which was
meant, so it does not guess: it applies one documented rule — ISO weeks,
Monday-start — and returns `week_start` / `week_end` alongside the answer so
the agent can show the user which week it landed on. A wrong date the user
can see is wrong is recoverable; a wrong date presented bare is not.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from narranexus.platform.module_system import caller_user_id_from_request
from narranexus.platform.utils.timezone import (
    DEFAULT_TIMEZONE,
    WEEKDAY_NAMES,
    resolve_timezone,
    to_user_timezone,
    utc_now,
)

#: Accepted `unit` values; how an offset of 1 moves the reference is in the
#: if/elif inside `resolve_relative_date`.
_UNITS = ("day", "week", "month", "year")

#: Weekday names the model may pass, lowercased. Chinese forms are accepted
#: because the model is as likely to echo the user's phrasing as to
#: translate it, and rejecting "周五" would push it back into guessing.
_WEEKDAYS: Dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4,
    "星期六": 5, "星期日": 6, "星期天": 6, "周天": 6,
}


async def _caller_timezone(user_id: str = "") -> str:
    """Resolve the turn owner's IANA timezone, server-side where possible.

    The header wins over the argument for the same reason it does everywhere
    else in this codebase: `user_id` is a model-filled parameter, and a
    model that invents one would silently shift every date this tool returns
    by that user's UTC offset — the exact class of error the tool exists to
    prevent. The argument is only a fallback for callers with no header
    (older adapters, direct curl).

    Fail-open to UTC: a tool that refuses to answer teaches the model to go
    back to doing the arithmetic itself, which is strictly worse than an
    answer in a stated frame.
    """
    resolved = caller_user_id_from_request() or (user_id or "").strip()
    if not resolved:
        return DEFAULT_TIMEZONE
    try:
        from narranexus.platform.repository import UserRepository
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        return resolve_timezone(await UserRepository(db).get_user_timezone(resolved))
    except Exception as e:  # noqa: BLE001 — a tz lookup must not fail the tool
        logger.warning(f"[date_tool] timezone lookup failed for {resolved}: {e}; using UTC")
        return DEFAULT_TIMEZONE


def _today_in(tz: str) -> date:
    local = to_user_timezone(utc_now(), tz)
    return (local or utc_now()).date()


#: Trailing human-readable annotation on `format_now_for_agent` output —
#: the ` (Tuesday, Asia/Shanghai)` tail. Stripped before parsing; see
#: `_parse_date`.
_TRAILING_ANNOTATION = re.compile(r"\s*\([^)]*\)\s*$")


def _parse_date(value: str, tz: str) -> Optional[date]:
    """Parse an agent-supplied date.

    Accepts `YYYY-MM-DD`, full ISO timestamps (with or without offset /
    trailing Z), and — critically — **the two formats this platform itself
    renders for agents to read**:

        2026-07-31 00:30 +08:00                              (timeline rows)
        2026-08-08 09:00:00 +08:00 (Tuesday, Asia/Shanghai)  (ground-truth now)

    That last one is why the annotation strip exists. The prompts tell the
    agent to pass a date it saw — "pass that message's date as `reference`",
    "confirm with compare_dates" — and the single most likely thing for a
    model to pass is a token it can literally see. If the parser rejects the
    platform's own rendering, the tool answers `bad_reference` / `bad_date`,
    and by this file's own argument the model then goes back to doing the
    arithmetic itself: the exact step these tools exist to remove. The
    failure would also be invisible — a structured error in a tool result,
    nothing in the audit trail, and a user who just sees a wrong date again.

    (The space between time and offset parses natively: this project requires
    Python >= 3.13, whose `fromisoformat` accepts it. Only the parenthesised
    tail needs removing.)

    A timestamp that carries a frame is converted into the user's before its
    date is taken — dropping that would reintroduce the off-by-one this whole
    change set is about, so the strip is deliberately narrow: it removes the
    annotation and nothing else.
    """
    raw = _TRAILING_ANNOTATION.sub("", (value or "").strip())
    if not raw:
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.date()
        local = to_user_timezone(parsed, tz)
        return (local or parsed).date()
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    """Shift by calendar months, clamping to the end of a shorter month.

    Jan 31 + 1 month is Feb 28/29, not an error and not Mar 3. Clamping is
    the behaviour a person means by "next month", and it keeps the tool
    total — there is no input for which it has to give up.
    """
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def _describe(d: date, today: date, tz: str) -> Dict[str, Any]:
    """The answer shape shared by both tools.

    Always carries `is_past` / `is_today` / `days_from_today` next to the
    date, because "which day is it" and "has it happened yet" are the two
    questions that got answered wrong, and returning only the first invites
    the model to derive the second by hand.
    """
    delta = (d - today).days
    return {
        "date": d.isoformat(),
        "weekday": WEEKDAY_NAMES[d.weekday()],
        "days_from_today": delta,
        "is_past": delta < 0,
        "is_today": delta == 0,
        "is_future": delta > 0,
        "today": today.isoformat(),
        "today_weekday": WEEKDAY_NAMES[today.weekday()],
        "timezone": tz,
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="resolve_relative_date",
        description=(
            "Turn a relative time expression into an exact calendar date. Use "
            "this INSTEAD of working the date out yourself — whenever you or "
            "the user say things like 'next Friday' / '下周五' / 'tomorrow' / "
            "'后天' / 'in 3 weeks' / 'end of next month'.\n"
            "\n"
            "You do the language, this tool does the arithmetic. Break the "
            "phrase into (unit, offset, weekday):\n"
            "  tomorrow / 明天          → unit='day',   offset=1\n"
            "  the day after tomorrow / 后天 → unit='day', offset=2\n"
            "  yesterday / 昨天         → unit='day',   offset=-1\n"
            "  this Friday / 这周五     → unit='week',  offset=0,  weekday='friday'\n"
            "  next Friday / 下周五     → unit='week',  offset=1,  weekday='friday'\n"
            "  last Monday / 上周一     → unit='week',  offset=-1, weekday='monday'\n"
            "  in 3 weeks / 三周后      → unit='week',  offset=3\n"
            "  next month / 下个月      → unit='month', offset=1\n"
            "\n"
            "Weeks are ISO weeks starting MONDAY, so offset=1 with a weekday "
            "means that weekday in the following Monday-to-Sunday week. The "
            "reply includes `week_start`/`week_end` — when the user's phrasing "
            "was ambiguous (English 'next Friday' genuinely is), state the "
            "resolved date and its week back to them rather than assuming.\n"
            "\n"
            "`reference` defaults to today in the user's timezone; pass a date "
            "only when anchoring to some other day (e.g. resolving what "
            "'下周五' meant in a message sent last week — pass that message's "
            "date). The reply also carries `is_past`/`days_from_today`, so you "
            "never need a second call to check whether the date has passed."
        ),
    )
    async def resolve_relative_date(
        unit: str,
        offset: int,
        weekday: str = "",
        reference: str = "",
        user_id: str = "",
    ) -> dict:
        """Handler for `resolve_relative_date`; contract is in `description`.

        `weekday` and `reference` are `str = ""` rather than `Optional[str]`
        on purpose: FastMCP renders Optional as `anyOf:[X,null]`, which
        strict-schema providers reject at the REQUEST level — the whole call
        fails, not just this tool.
        """
        try:
            tz = await _caller_timezone(user_id)
            today = _today_in(tz)

            anchor = today
            if reference:
                parsed = _parse_date(reference, tz)
                if parsed is None:
                    return {
                        "error": f"could not parse reference={reference!r}; "
                                 "use YYYY-MM-DD or a full ISO timestamp",
                        "code": "bad_reference",
                    }
                anchor = parsed

            u = (unit or "").strip().lower()
            if u not in _UNITS:
                return {
                    "error": f"unit must be one of {list(_UNITS)}, got {unit!r}",
                    "code": "bad_unit",
                }

            wd_key = (weekday or "").strip().lower()
            if wd_key and wd_key not in _WEEKDAYS:
                return {
                    "error": f"unknown weekday {weekday!r}; use an English "
                             "weekday name or 周一…周日",
                    "code": "bad_weekday",
                }

            if u == "day":
                target = anchor + timedelta(days=offset)
            elif u == "week":
                target = anchor + timedelta(weeks=offset)
            elif u == "month":
                target = _add_months(anchor, offset)
            else:  # year
                target = _add_months(anchor, offset * 12)

            # Snap to a weekday WITHIN the resolved week. Applied after the
            # offset so "next Friday" is the Friday of next week, not "seven
            # days after the coming Friday" — the two differ whenever the
            # reference day is itself past that weekday.
            week_monday = target - timedelta(days=target.weekday())
            if wd_key:
                target = week_monday + timedelta(days=_WEEKDAYS[wd_key])
                week_monday = target - timedelta(days=target.weekday())

            result = _describe(target, today, tz)
            result.update({
                "reference": anchor.isoformat(),
                "reference_weekday": WEEKDAY_NAMES[anchor.weekday()],
                "week_start": week_monday.isoformat(),
                "week_end": (week_monday + timedelta(days=6)).isoformat(),
                "input": {"unit": u, "offset": offset, "weekday": wd_key or None},
            })
            return result
        except Exception as e:  # noqa: BLE001 — never take the turn down
            logger.exception(f"[date_tool] resolve_relative_date failed: {e}")
            return {"error": str(e), "code": "internal_error"}

    @mcp.tool(
        name="compare_dates",
        description=(
            "Check where a date sits relative to another one — or to today. "
            "Use this before saying anything about whether something has "
            "happened, is happening, or is still ahead.\n"
            "\n"
            "The failure this prevents: an agent holding a correctly-recorded "
            "date ('the event is on 2026-08-07') told the user on 2026-08-08 "
            "that today was the event day. The date was right; the comparison "
            "was not. Any time you are about to write '今天是…' / 'today is "
            "the day' / 'that's coming up' / 'that already passed', confirm it "
            "here first and quote what comes back.\n"
            "\n"
            "`date_a` and `date_b` accept YYYY-MM-DD or a full ISO timestamp. "
            "`date_b` defaults to TODAY in the user's timezone, which is the "
            "common case — pass one date and read `is_past` / `is_today` / "
            "`days_from_today`. Comparison is by calendar day in the user's "
            "timezone, so a timestamp late at night does not read as the next "
            "day the way a raw UTC comparison would."
        ),
    )
    async def compare_dates(
        date_a: str,
        date_b: str = "",
        user_id: str = "",
    ) -> dict:
        """Handler for `compare_dates`; contract is in `description`."""
        try:
            tz = await _caller_timezone(user_id)
            today = _today_in(tz)

            a = _parse_date(date_a, tz)
            if a is None:
                return {
                    "error": f"could not parse date_a={date_a!r}; use "
                             "YYYY-MM-DD or a full ISO timestamp",
                    "code": "bad_date",
                }

            b = today if not date_b else _parse_date(date_b, tz)
            if b is None:
                return {
                    "error": f"could not parse date_b={date_b!r}; use "
                             "YYYY-MM-DD or a full ISO timestamp",
                    "code": "bad_date",
                }

            # Two separate readings, never conflated: `is_past` / `is_today` /
            # `days_from_today` always mean "against today", and
            # `days_between` / `order` always mean "against date_b". When b
            # defaults to today the two agree; when the caller passed an
            # explicit b they must not silently swap meaning, because the
            # is_past family is what the model quotes when it decides whether
            # something has happened.
            delta = (a - b).days
            result = _describe(a, today, tz)
            result.update({
                "compared_to": b.isoformat(),
                "compared_to_weekday": WEEKDAY_NAMES[b.weekday()],
                "compared_to_is_today": b == today,
                "days_between": delta,
                "order": "after" if delta > 0 else ("before" if delta < 0 else "same_day"),
            })
            return result
        except Exception as e:  # noqa: BLE001 — never take the turn down
            logger.exception(f"[date_tool] compare_dates failed: {e}")
            return {"error": str(e), "code": "internal_error"}
