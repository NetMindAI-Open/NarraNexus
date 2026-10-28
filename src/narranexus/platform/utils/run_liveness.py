"""
@file_name: run_liveness.py
@author:
@date: 2026-09-10
@description: The one cross-process rule for "is this run actually alive?".

Pure data + arithmetic over an ``events`` row: the heartbeat cadence, the
staleness threshold and ``run_is_live``. It lives in ``utils`` (a leaf that
imports nothing from the platform above it) so BOTH of its consumers can
import it at module level without a cycle:

* ``agent_runtime.run_recorder`` — owns the run state machine and the stale
  sweep, and re-exports these names for its existing callers;
* ``agent_framework.loop.circuit_breaker`` — asks whether a half-open probe's
  claimant may still be running before re-claiming or releasing it.

Before this module the breaker imported ``run_recorder`` lazily while
``run_recorder`` imported the breaker lazily — two lazy imports each
papering over the other half of a loop<->runtime cycle. The release path
and the sweep must keep sharing the SAME ``run_is_live`` object (one answer
to liveness), which is why this is a move, not a copy.

Read-side only: nothing here may be used to stop or mutate a live run. A
genuinely long run keeps beating and stays live (binding rule #14).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from narranexus.platform.utils.timezone import utc_now

# Heartbeat cadence — every N seconds the recorder's heartbeat task bumps
# events.last_event_at, even if no stream events fired. Used by the stale-run
# sweep and observability read-sides to distinguish a healthy long thinking
# pass from a dead process.
HEARTBEAT_INTERVAL_S = 30

# The non-terminal run state (``events.state``); the terminal states live with
# the state machine in ``run_recorder``.
STATE_RUNNING = "running"

# An events row stuck at state='running' is only trusted as alive while its
# heartbeat is fresh. After 3 missed beats the run is presumed dead — its task
# died without finalize (process killed mid-run, or the terminal DB write
# failed). Shared by every read-side consumer (agents listing, WS observe
# endpoint, stale sweep, the breaker's probe-claimant check) so "is this run
# actually alive?" has ONE answer.
RUN_STALE_AFTER_S = HEARTBEAT_INTERVAL_S * 3


def parse_db_utc(ts: Any) -> Optional[datetime]:
    """Parse a stored UTC timestamp (SQLite returns ISO strings, MySQL
    returns datetime) into a tz-aware UTC datetime. Returns None when the
    value is absent or unparseable."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.rstrip("Z"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def run_is_live(events_row: dict, now: Optional[datetime] = None) -> bool:
    """Whether a 'running' events row still has a fresh heartbeat. Falls
    back to started_at when the first beat hasn't fired yet. Fails open
    (treats as live) when no parseable timestamp exists, so we never
    declare dead a run that might genuinely be running."""
    now = now or utc_now()
    parsed = parse_db_utc(events_row.get("last_event_at")) or parse_db_utc(
        events_row.get("started_at")
    )
    if parsed is None:
        return True
    return (now - parsed) <= timedelta(seconds=RUN_STALE_AFTER_S)


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "RUN_STALE_AFTER_S",
    "STATE_RUNNING",
    "parse_db_utc",
    "run_is_live",
]
