"""
@file_name: test_temporal_guard.py
@date: 2026-08-18
@description: Lock the diagnostic date-claim scanner.

Two halves matter here and they pull in opposite directions:

  * it has to catch the real thing — an agent stating the wrong date for
    "today", in either language;
  * it has to stay quiet otherwise, because a probe that cries wolf gets
    muted, and a muted probe reads as coverage while providing none.

So the false-positive tests below are not padding. They are the half of the
contract that decides whether anyone will still trust this signal in three
months.
"""

from datetime import datetime, timezone as dt_timezone

import pytest

from narranexus.platform.utils import temporal_guard
from narranexus.platform.utils.temporal_guard import (
    AUDIT_EVENT_DATE_MISMATCH,
    AUDIT_SERVICE,
    record_date_claim_mismatches,
    scan_reply_for_date_claims,
)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    """Saturday 2026-08-08, 09:00 in Shanghai (01:00 UTC).

    Chosen to reproduce the reported incident: the event was on 2026-08-07
    and the agent said, on the 8th, that today was the day.
    """
    monkeypatch.setattr(
        temporal_guard, "utc_now",
        lambda: datetime(2026, 8, 8, 1, 0, tzinfo=dt_timezone.utc),
    )


TZ = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# Catches the real thing
# ---------------------------------------------------------------------------

def test_reproduces_the_reported_incident():
    found = scan_reply_for_date_claims("今天是 8 月 7 日，就是活动当天！", TZ)
    assert len(found) == 1
    assert found[0].kind == "date"
    assert found[0].claimed == "2026-08-07"
    assert found[0].actual == "2026-08-08"


@pytest.mark.parametrize("text,claimed", [
    ("今天是2026-08-07", "2026-08-07"),
    ("现在是 2026/8/7", "2026-08-07"),
    ("今日是 8月7号", "2026-08-07"),
    ("目前是2026年8月7日", "2026-08-07"),
    ("Today is 2026-08-07.", "2026-08-07"),
    ("today is August 7", "2026-08-07"),
    ("Today is Aug 7th, so we still have time.", "2026-08-07"),
])
def test_wrong_date_claims_are_caught_in_both_languages(text, claimed):
    found = scan_reply_for_date_claims(text, TZ)
    assert [f.claimed for f in found] == [claimed]


@pytest.mark.parametrize("text,claimed", [
    ("今天是周五", "Friday"),
    ("今天星期五哦", "Friday"),
    ("Today is Friday", "Friday"),
    ("今天礼拜天", "Sunday"),
])
def test_wrong_weekday_claims_are_caught(text, claimed):
    found = scan_reply_for_date_claims(text, TZ)
    assert len(found) == 1
    assert found[0].kind == "weekday"
    assert found[0].claimed == claimed
    assert found[0].actual == "Saturday"


def test_multiple_claims_are_reported_separately():
    found = scan_reply_for_date_claims("今天是 8 月 7 日，今天是周五。", TZ)
    assert {f.kind for f in found} == {"date", "weekday"}


def test_excerpt_is_bounded_and_single_line():
    """The audit row must be reviewable without carrying an entire reply
    around — these rows outlive the conversation."""
    text = "x" * 500 + "\n今天是 8 月 7 日\n" + "y" * 500
    found = scan_reply_for_date_claims(text, TZ)
    assert len(found) == 1
    assert "\n" not in found[0].excerpt
    assert len(found[0].excerpt) < 200


# ---------------------------------------------------------------------------
# Stays quiet otherwise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "今天是 8 月 8 日",
    "今天是2026-08-08",
    "今天是周六",
    "Today is Saturday",
    "Today is 2026-08-08",
])
def test_correct_claims_produce_nothing(text):
    assert scan_reply_for_date_claims(text, TZ) == []


@pytest.mark.parametrize("text", [
    "活动是 8 月 7 日举行的",           # a date, but not a claim about today
    "我们在 2026-08-07 见过面",
    "下周五我提醒你",                    # relative, nothing asserted about today
    "The deadline was August 7.",
    "会议改到周五了",                    # a weekday, not "today is"
    "The meeting is on Friday.",
    "",
    "   ",
])
def test_non_claims_are_not_flagged(text):
    """The scanner checks assertions about NOW. Any other date in the reply
    is context it has no business grading — flagging those is how the signal
    would become unreadable."""
    assert scan_reply_for_date_claims(text, TZ) == []


def test_year_less_claims_are_read_in_the_current_year():
    """"8月8日" written in August 2026 means 2026 — not year 1, and not a
    mismatch by a whole year."""
    assert scan_reply_for_date_claims("今天是 8 月 8 日", TZ) == []


def test_impossible_dates_are_skipped_rather_than_flagged():
    """The point is to measure comparison errors, not calendar trivia."""
    assert scan_reply_for_date_claims("今天是 2 月 30 日", TZ) == []


def test_english_word_after_today_that_is_not_a_month_is_ignored():
    assert scan_reply_for_date_claims("Today is looking busy 8", TZ) == []


def test_claim_is_evaluated_in_the_users_timezone():
    """01:00 UTC on the 8th is still the 7th in New York, so the SAME
    sentence is correct there and wrong in Shanghai. Getting this backwards
    would make the probe fire on every overnight reply."""
    assert scan_reply_for_date_claims("今天是 8 月 7 日", "America/New_York") == []
    assert len(scan_reply_for_date_claims("今天是 8 月 7 日", "Asia/Shanghai")) == 1


def test_unknown_timezone_degrades_to_utc_without_raising():
    found = scan_reply_for_date_claims("今天是 8 月 7 日", "Not/A/Zone")
    assert len(found) == 1
    assert found[0].timezone == "UTC"


# ---------------------------------------------------------------------------
# Persistence — and the promise that it never intervenes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mismatch_is_written_to_service_audit(db_client):
    import json

    found = await record_date_claim_mismatches(
        db_client,
        "今天是 8 月 7 日，就是活动当天！",
        TZ,
        agent_id="agent_x",
        user_id="u_x",
        event_id="evt_x",
    )
    assert len(found) == 1

    rows = await db_client.get("service_audit", {"service": AUDIT_SERVICE})
    assert len(rows) == 1
    assert rows[0]["event_type"] == AUDIT_EVENT_DATE_MISMATCH
    detail = json.loads(rows[0]["detail"])
    assert detail["claimed"] == "2026-08-07"
    assert detail["actual"] == "2026-08-08"
    assert detail["agent_id"] == "agent_x"
    assert detail["event_id"] == "evt_x"


@pytest.mark.asyncio
async def test_clean_reply_writes_nothing(db_client):
    found = await record_date_claim_mismatches(db_client, "今天是 8 月 8 日", TZ)
    assert found == []
    assert await db_client.get("service_audit", {"service": AUDIT_SERVICE}) == []


@pytest.mark.asyncio
async def test_audit_write_failure_is_swallowed():
    """A reporting probe must not be able to break the thing it reports on —
    this is the whole reason it is safe to leave switched on.

    Note who swallows it: since 2026-08-18 the write goes through
    `ServiceAuditRepository.record`, which is itself best-effort and never
    raises. So this test now pins the END-TO-END guarantee rather than a
    try/except inside `temporal_guard` — the fake only needs `insert`,
    because that is all the repository calls.
    """
    class _ExplodingDB:
        async def insert(self, *_a, **_kw):
            raise RuntimeError("db down")

    found = await record_date_claim_mismatches(
        _ExplodingDB(), "今天是 8 月 7 日", TZ
    )
    # The scan result still comes back; only the persistence was lost.
    assert len(found) == 1


@pytest.mark.asyncio
async def test_scanner_never_mutates_the_reply(db_client):
    """Stated as a test because it is the design constraint, not an
    implementation detail: 铁律 #15/#16 put output rewriting out of bounds.
    The function returns findings and takes the text by value."""
    original = "今天是 8 月 7 日，就是活动当天！"
    text = original
    await record_date_claim_mismatches(db_client, text, TZ)
    assert text == original
