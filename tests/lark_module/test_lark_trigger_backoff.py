"""
@file_name: test_lark_trigger_backoff.py
@author: Bin Liang
@date: 2026-04-21
@description: H-1 fix — WS reconnect backoff resets after a healthy run.

Before the fix:
  - Line 387 `backoff = 5` ran unconditionally (dead code comment claimed
    "ran > 60s" gating that never existed).
  - Line 398 then doubled it every iteration.
  - Net effect: every disconnect compounded the backoff toward the
    120s cap even after hours of healthy session.

After the fix:
  - `_compute_next_backoff(current, ran_seconds)` returns `base` when
    the just-ended WS session ran >= 60s (a real connection), else
    doubles `current` up to `max_backoff`.
  - The `_subscribe_loop` records `time.monotonic()` at thread start
    and feeds (monotonic_now - start) to this helper.
"""
from __future__ import annotations

from narranexus_plugins.lark_module.lark_trigger import (
    _compute_next_backoff,
)


def test_healthy_long_session_resets_backoff_to_base():
    # Current backoff was grown to 80s after a string of short failures
    next_b = _compute_next_backoff(current=80, ran_seconds=300.0)
    assert next_b == 5


def test_boundary_exactly_60s_counts_as_healthy():
    next_b = _compute_next_backoff(current=40, ran_seconds=60.0)
    assert next_b == 5


def test_short_failure_doubles_backoff():
    next_b = _compute_next_backoff(current=5, ran_seconds=2.0)
    assert next_b == 10


def test_backoff_caps_at_max():
    next_b = _compute_next_backoff(current=80, ran_seconds=1.0, max_backoff=120)
    assert next_b == 120


def test_backoff_already_at_cap_stays_capped():
    next_b = _compute_next_backoff(current=120, ran_seconds=1.0, max_backoff=120)
    assert next_b == 120


def test_zero_seconds_still_doubles():
    # A WS that never really connected (ran == 0) counts as failure
    next_b = _compute_next_backoff(current=5, ran_seconds=0.0)
    assert next_b == 10
