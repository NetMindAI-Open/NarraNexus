"""
@file_name: test_active_run_liveness.py
@author: Bin Liang
@date: 2026-05-21
@description: Heartbeat-liveness filter for the agents-listing active_run.

Regression (debug/20260521-agent-running-halo): the sidebar agent avatar
shows a breathing "running" halo whenever AgentInfo.active_run is set.
active_run is derived from any events row with state='running'. A run whose
task died without _finalize (process killed mid-run, or the terminal DB
write failed) leaves that row stuck at 'running' until the next backend
restart (startup reconcile). The halo then pulses forever for an agent that
is not running.

Fix: GET /api/auth/agents only surfaces a 'running' row as active_run while
its heartbeat (last_event_at, falling back to started_at) is fresh.
BackgroundRun bumps last_event_at every HEARTBEAT_INTERVAL_S (30s); a row
that has missed 3 beats is treated as dead. A genuinely long-running agent
keeps beating and stays live — we never stop or mutate a run, this is a
read-side liveness filter only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.routes.auth as auth
from xyz_agent_context.agent_runtime.background_run import HEARTBEAT_INTERVAL_S


NOW = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
STALE_AFTER = HEARTBEAT_INTERVAL_S * 3  # 90s


def _iso_z(dt: datetime) -> str:
    """Mirror the on-disk SQLite string format (ISO 8601, 'Z' suffix)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Fresh heartbeat → live ──────────────────────────────────────────────

def test_recent_heartbeat_is_live():
    row = {"last_event_at": _iso_z(NOW - timedelta(seconds=10))}
    assert auth._run_is_live(row, now=NOW) is True


def test_heartbeat_just_inside_window_is_live():
    row = {"last_event_at": _iso_z(NOW - timedelta(seconds=STALE_AFTER - 1))}
    assert auth._run_is_live(row, now=NOW) is True


# ── Dead heartbeat → not live ───────────────────────────────────────────

def test_old_heartbeat_is_dead():
    row = {"last_event_at": _iso_z(NOW - timedelta(seconds=STALE_AFTER + 30))}
    assert auth._run_is_live(row, now=NOW) is False


def test_hours_old_heartbeat_is_dead():
    row = {"last_event_at": _iso_z(NOW - timedelta(hours=5))}
    assert auth._run_is_live(row, now=NOW) is False


# ── Fallback to started_at when no heartbeat yet ────────────────────────

def test_no_heartbeat_falls_back_to_started_at_fresh():
    # Run just started, heartbeat task hasn't fired its first beat yet.
    row = {"last_event_at": None, "started_at": _iso_z(NOW - timedelta(seconds=5))}
    assert auth._run_is_live(row, now=NOW) is True


def test_no_heartbeat_old_started_at_is_dead():
    row = {"last_event_at": None, "started_at": _iso_z(NOW - timedelta(minutes=10))}
    assert auth._run_is_live(row, now=NOW) is False


# ── Fail-open: missing / unparseable timestamps don't hide a run ─────────

def test_missing_all_timestamps_fails_open():
    # No timestamp info at all — don't silently hide a possibly-live run.
    assert auth._run_is_live({}, now=NOW) is True


def test_unparseable_timestamp_fails_open():
    assert auth._run_is_live({"last_event_at": "not-a-date"}, now=NOW) is True


# ── Naive datetime (MySQL driver returns datetime, not str) ─────────────

def test_naive_datetime_treated_as_utc():
    naive = (NOW - timedelta(seconds=10)).replace(tzinfo=None)
    assert auth._run_is_live({"last_event_at": naive}, now=NOW) is True


def test_aware_datetime_dead():
    aware = NOW - timedelta(hours=2)
    assert auth._run_is_live({"last_event_at": aware}, now=NOW) is False
