"""
@file_name: test_timezone_format_for_api.py
@author: Bin Liang
@date: 2026-09-09
@description: `format_for_api` formats ONE datetime (or a SQLite timestamp
string). Fed anything else it used to hit its except-branch and return
`str(value)` — for a whole `model_dump()` that is a Python repr the caller
then served as a timestamp (B-18: the bulletin panel rendered blank rows
with only a warning line to say why). A type error must be loud at the
call site, while the two legitimate inputs and the fail-soft string branch
keep behaving exactly as before.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.utils.timezone import format_for_api, to_user_timezone


def test_datetime_and_sqlite_string_still_format_to_z_suffixed_iso():
    aware = datetime(2025, 1, 15, 14, 30, tzinfo=timezone(timedelta(hours=8)))
    assert format_for_api(aware) == "2025-01-15T06:30:00Z"
    assert format_for_api(datetime(2025, 1, 15, 14, 30)) == "2025-01-15T14:30:00Z"  # naive = UTC
    assert format_for_api("2025-01-15T14:30:00") == "2025-01-15T14:30:00Z"
    assert format_for_api(None) is None


def test_an_unparseable_string_is_returned_as_is():
    """The SQLite branch's documented fail-soft — must not be tightened."""
    assert format_for_api("Jan 15, 2025") == "Jan 15, 2025"


@pytest.mark.parametrize("bad", [{"created_at": "x"}, ["2025-01-15"], 1736951400, object()])
def test_a_non_datetime_raises_type_error_instead_of_returning_its_repr(bad):
    with pytest.raises(TypeError, match="format_for_api expects"):
        format_for_api(bad)


@pytest.mark.parametrize("bad", [{"ts": "x"}, ["2025-01-15"], 1736951400])
def test_to_user_timezone_raises_type_error_under_its_own_name(bad):
    """The same guard lives on the other entry point and names itself, so a
    traceback from the agent-prompt path points at the function that raised."""
    with pytest.raises(TypeError, match="to_user_timezone expects"):
        to_user_timezone(bad, "UTC")
