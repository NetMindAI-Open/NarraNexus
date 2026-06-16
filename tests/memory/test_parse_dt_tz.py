"""
@file_name: test_parse_dt_tz.py
@author: Bin Liang
@date: 2026-06-09
@description: _parse_dt must return timezone-aware UTC.

Regression: MySQL DATETIME comes back as a NAIVE datetime and offset-less ISO
strings parse naive, so `utc_now() - _parse_dt(x)` raised "can't subtract
offset-naive and offset-aware datetimes" — crashing the memory consolidation
worker every pass on the cloud (MySQL) backend.
"""
from datetime import datetime, timezone

from xyz_agent_context.memory.record import _parse_dt
from xyz_agent_context.utils.timezone import utc_now


def test_naive_datetime_object_becomes_utc_aware():
    naive = datetime(2026, 6, 9, 10, 0, 0)  # e.g. MySQL aiomysql return
    out = _parse_dt(naive)
    assert out.tzinfo is not None
    assert out.utcoffset().total_seconds() == 0


def test_naive_iso_string_becomes_utc_aware():
    out = _parse_dt("2026-06-09T10:00:00")
    assert out.tzinfo is not None


def test_space_separated_string_becomes_utc_aware():
    out = _parse_dt("2026-06-09 10:00:00")
    assert out is not None and out.tzinfo is not None


def test_aware_string_is_preserved():
    out = _parse_dt("2026-06-09T10:00:00+00:00")
    assert out.tzinfo is not None


def test_subtraction_against_utc_now_does_not_raise():
    # the exact operation that crashed the consolidation worker
    last = _parse_dt(datetime(2026, 6, 9, 9, 0, 0))  # naive (MySQL)
    delta = (utc_now() - last).total_seconds()
    assert delta > 0


def test_empty_and_none_still_return_none():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("garbage") is None
