"""
@file_name: temporal_guard.py
@author: NarraNexus
@date: 2026-08-18
@description: Diagnostic check for date claims an agent makes about "now".

What this is
------------
An observability probe, NOT a filter. It reads what the agent already said
and records a signal when a claim about today's date contradicts the turn's
ground-truth clock. It never rewrites a reply, never blocks delivery, never
asks the model to try again, and never influences agent_loop in any way.

That restraint is the design, not a limitation. 铁律 #15 and #16 say the
platform is not the interruption source and must not degrade what the user
sees; an output filter that "corrects" the model would be exactly that. And
the incident lessons in CLAUDE.md are explicit that the useful thing here is
an L2/L3 signal in the database (§4, §5): a missing or contradicted business
event is evidence you can query, whereas a log grep is only as good as the
keyword someone guessed.

So the deal is: prompts and tools try to prevent the mistake; this tells us
honestly how often prevention is failing, on real traffic, without touching
the traffic.

What it checks
--------------
Only DIRECT assertions about the current date — "今天是 8 月 8 日", "today is
Friday", "现在是 2026-08-08". These are checkable against one number with no
interpretation, which is what keeps the signal worth reading.

It deliberately does NOT try to catch the general case ("the event is coming
up", "that's next week"), because deciding whether those are wrong needs the
surrounding context and would produce a stream of maybes. A probe nobody
trusts gets muted, and a muted probe is worse than none: it looks like
coverage. Narrow and reliable beats broad and noisy.

Chinese and English are both handled because both appear in production
replies, often in the same message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from loguru import logger

from narranexus.platform.utils.timezone import (
    DEFAULT_TIMEZONE,
    WEEKDAY_NAMES,
    resolve_timezone,
    to_user_timezone,
    utc_now,
)

#: The audit `service` value; one place so queries and writes cannot drift.
AUDIT_SERVICE = "temporal_guard"

#: Audit `event_type` for a contradicted claim.
AUDIT_EVENT_DATE_MISMATCH = "date_claim_mismatch"

# "today" in the two languages that show up in replies. The Chinese forms
# include 现在/目前 because an agent stating the date often frames it as
# "现在是…" rather than "今天是…".
_TODAY_MARKERS = r"(?:今天|今日|现在|目前|本日|today|Today|TODAY)"

# ISO-ish date: 2026-08-08 / 2026/8/8.
_ISO_DATE = re.compile(
    rf"{_TODAY_MARKERS}\s*(?:是|为|is|＝|=|:|：)?\s*"
    r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?"
)

# Month/day with no year: 8月8日 / 8月8号 / August 8 / Aug 8th.
_CN_MONTH_DAY = re.compile(
    rf"{_TODAY_MARKERS}\s*(?:是|为|＝|=|:|：)?\s*(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*[日号]"
)

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

_EN_MONTH_DAY = re.compile(
    rf"{_TODAY_MARKERS}\s*(?:is|＝|=|:|：)?\s*"
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?",
)

_WEEKDAY_TOKENS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6,
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5,
    "周日": 6, "周天": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4,
    "星期六": 5, "星期日": 6, "星期天": 6,
    "礼拜一": 0, "礼拜二": 1, "礼拜三": 2, "礼拜四": 3, "礼拜五": 4,
    "礼拜六": 5, "礼拜日": 6, "礼拜天": 6,
}

_WEEKDAY_CLAIM = re.compile(
    rf"{_TODAY_MARKERS}\s*(?:是|为|is|＝|=|:|：)?\s*"
    r"(周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
)


@dataclass(frozen=True)
class DateClaimMismatch:
    """One contradicted claim about the current date."""

    kind: str          # "date" | "weekday"
    claimed: str       # what the reply asserted, normalised
    actual: str        # what the clock says
    excerpt: str       # the matched span, for eyeballing false positives
    timezone: str

    def as_detail(self) -> dict:
        return {
            "kind": self.kind,
            "claimed": self.claimed,
            "actual": self.actual,
            "excerpt": self.excerpt,
            "timezone": self.timezone,
        }


def _today_in(user_tz: str) -> date:
    local = to_user_timezone(utc_now(), user_tz)
    return (local or utc_now()).date()


def _excerpt(text: str, match: re.Match, width: int = 40) -> str:
    """A window around the match, so a human reviewing the audit row can
    judge a false positive without pulling the whole reply (which may be
    long and is not ours to copy around wholesale)."""
    start = max(0, match.start() - width)
    end = min(len(text), match.end() + width)
    return text[start:end].replace("\n", " ").strip()


def scan_reply_for_date_claims(
    text: str,
    user_tz: str = DEFAULT_TIMEZONE,
) -> List[DateClaimMismatch]:
    """Return the date claims in `text` that contradict today's date.

    Pure and side-effect free — no clock beyond `utc_now()`, no I/O — so it
    is cheap enough to run on every delivered reply and trivial to test.

    A claim with no year (8月8日 / August 8) is read as the current year;
    that is what a person writing it means, and it keeps a December-to-
    January reply from registering as a mismatch by a whole year.
    """
    if not text or not text.strip():
        return []

    tz = resolve_timezone(user_tz)
    today = _today_in(tz)
    found: List[DateClaimMismatch] = []

    def _add_date(claimed: Optional[date], match: re.Match) -> None:
        if claimed is None or claimed == today:
            return
        found.append(DateClaimMismatch(
            kind="date",
            claimed=claimed.isoformat(),
            actual=today.isoformat(),
            excerpt=_excerpt(text, match),
            timezone=tz,
        ))

    for m in _ISO_DATE.finditer(text):
        _add_date(_safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m)

    for m in _CN_MONTH_DAY.finditer(text):
        _add_date(_safe_date(today.year, int(m.group(1)), int(m.group(2))), m)

    for m in _EN_MONTH_DAY.finditer(text):
        month = _MONTH_NAMES.get(m.group(1).lower())
        if month is None:
            continue  # "today is not…" and friends — not a date claim.
        _add_date(_safe_date(today.year, month, int(m.group(2))), m)

    for m in _WEEKDAY_CLAIM.finditer(text):
        claimed_idx = _WEEKDAY_TOKENS.get(m.group(1).lower())
        if claimed_idx is None or claimed_idx == today.weekday():
            continue
        found.append(DateClaimMismatch(
            kind="weekday",
            claimed=WEEKDAY_NAMES[claimed_idx],
            actual=WEEKDAY_NAMES[today.weekday()],
            excerpt=_excerpt(text, m),
            timezone=tz,
        ))

    return found


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    """Nonexistent dates (2 月 30 日) are a claim we cannot check, not a
    mismatch to report — the point is to measure comparison errors, not to
    grade the model's calendar trivia."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


async def record_date_claim_mismatches(
    db,
    text: str,
    user_tz: str,
    *,
    agent_id: str = "",
    user_id: str = "",
    event_id: str = "",
) -> List[DateClaimMismatch]:
    """Scan `text` and append one `service_audit` row per contradicted claim.

    Reuses `service_audit` rather than adding a table: the shape (service /
    event_type / detail / created_at) is exactly right, and a diagnostic
    that has not yet earned its keep should not also cost a migration.

    Writes go through `ServiceAuditRepository.record`, like every other
    `service_audit` producer in the codebase — the table name, the JSON
    serialisation and the best-effort semantics all live there, so a future
    change to the audit row's shape does not have to remember this file.
    `record()` never raises, which is also why there is no try/except here.

    Fail-open on every path. This function's whole value is that it can be
    switched on in production without anyone worrying about it — an audit
    write that raised into `step_4` would make a reporting probe capable of
    breaking the thing it reports on.
    """
    try:
        mismatches = scan_reply_for_date_claims(text, user_tz)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[TemporalGuard] scan failed: {e}")
        return []

    if not mismatches:
        return []

    from narranexus.platform.repository.service_audit_repository import (
        ServiceAuditRepository,
    )

    repo = ServiceAuditRepository(db)
    for m in mismatches:
        # WARNING, not ERROR: nothing is broken from the runtime's point of
        # view — the turn succeeded and the user got their reply. This is a
        # correctness signal for us, and over-severity is how alerts get
        # tuned out (incident lesson §3).
        logger.warning(
            f"[TemporalGuard] agent={agent_id} claimed {m.kind}={m.claimed} "
            f"but ground truth is {m.actual} ({m.timezone}) — excerpt: {m.excerpt!r}"
        )
        await repo.record(AUDIT_SERVICE, AUDIT_EVENT_DATE_MISMATCH, {
            **m.as_detail(),
            "agent_id": agent_id,
            "user_id": user_id,
            "event_id": event_id,
        })

    return mismatches
