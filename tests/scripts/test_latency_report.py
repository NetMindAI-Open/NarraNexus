"""
@file_name: test_latency_report.py
@date: 2026-08-14
@description: The percentile maths and log parsing behind PRD acceptance #1.

This report reads `[bus-timing]` / `[turn-timing]` straight out of
``~/.narranexus/logs``. It used to read two purpose-built tables; those were
dropped once it became clear the log files already carry the data AND are
already retained for 30 days (loguru: rotation="00:00", retention="30 days").
Adding two tables, two repositories and two hot-path writes bought a JOIN and
little else — see the mirror for the full reasoning.

Four things are pinned, all of which fail as "no data" rather than as an error,
which is the worst failure mode a diagnostic can have:

* the -1 sentinel is EXCLUDED, not read as zero. A hop whose message carried no
  `created_at` has no knowable queue wait; folding it in as 0ms reports the
  fastest hops we never had and can flip a FAIL into a PASS.
* percentiles are nearest-rank, so every number printed names a hop that really
  happened.
* log timestamps are LOCAL time (loguru's default) while the window is computed
  from `datetime.now()`. Mixing in a UTC cutoff silently drops several hours of
  the most recent data — the hours you actually care about.
* rotated logs are `.zip`. Reading only `*.log` sees today and nothing else,
  which quietly turns a 30-day report into a today-report.
"""
from __future__ import annotations

import importlib.util
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "diag_collector" / "latency_report.py"
)
_spec = importlib.util.spec_from_file_location("latency_report", _MODULE_PATH)
latency_report = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(latency_report)

_percentile = latency_report._percentile
_summarise = latency_report._summarise
_parse_hop = latency_report._parse_hop
_parse_turn = latency_report._parse_turn
_line_time = latency_report._line_time
_iter_log_lines = latency_report._iter_log_lines


# --- percentiles ------------------------------------------------------------

def test_percentile_is_nearest_rank_and_always_a_real_sample():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    for pct in (0.0, 0.25, 0.5, 0.9, 0.99, 1.0):
        assert _percentile(values, pct) in values, (
            "a percentile that is not one of the samples means interpolation "
            "crept in — the report would name a latency nobody experienced"
        )
    assert _percentile(values, 0.50) == 5
    assert _percentile(values, 0.90) == 9
    assert _percentile(values, 1.0) == 10


def test_percentile_handles_the_degenerate_sizes():
    assert _percentile([], 0.5) is None
    assert _percentile([42], 0.5) == 42
    assert _percentile([42], 0.99) == 42


def test_the_sentinel_is_excluded_not_counted_as_fast():
    """The failure this guards against changes the verdict, not just the digits."""
    values = [-1000] + [40_000] * 9
    line = _summarise("queue_wait", values, dropped=1)

    assert "n=9" in line, "the sentinel was counted as a sample"
    assert "40.00s" in line
    assert "1 unmeasurable, excluded" in line
    assert " 0.00s" not in line, (
        "the sentinel leaked in as a zero — that is the bug that would turn a "
        "failing p50 into a passing one"
    )


def test_summarise_survives_an_all_sentinel_sample():
    line = _summarise("queue_wait", [-1000, -1000], dropped=2)
    assert "n=0" in line
    assert "—" in line, "no samples must render as a dash, not as 0.00s"


@pytest.mark.parametrize("ms,expected", [(None, "—"), (0, "0.00s"), (1500, "1.50s")])
def test_fmt_ms(ms, expected):
    assert expected in latency_report._fmt_ms(ms)


# --- log parsing ------------------------------------------------------------

_HOP = (
    "2026-08-14 15:01:03.211 | INFO     | -------- -------------- | "
    "narranexus.platform.message_bus.message_bus_trigger:_handle_channel_batch:1291 - "
    "[bus-timing] agent=agent_a channel=ch_1 team=True batch=2 "
    "queue_wait_s=7.97 oldest_wait_s=8.50 turn_s=23.12 hop_s=31.10"
)
_TURN = (
    "2026-08-14 15:01:03.200 | INFO     | run_cbd evt_c9c | "
    "narranexus.platform.agent_runtime.agent_runtime:run:869 - "
    "[turn-timing] agent=agent_a event=evt_c9c source=WorkingSource.MESSAGE_BUS "
    "pre_s=0.00 setup_s=6.26 loop_s=16.80 persist_s=0.04 total_s=23.11 "
    "interrupted=False"
)


def test_a_hop_line_parses_into_milliseconds():
    hop = _parse_hop(_HOP)
    assert hop is not None
    assert hop["is_team"] is True
    assert hop["batch_size"] == 2
    assert hop["queue_wait_ms"] == 7970
    assert hop["oldest_wait_ms"] == 8500
    assert hop["turn_ms"] == 23120
    assert hop["hop_ms"] == 31100


def test_a_dm_hop_is_not_team():
    hop = _parse_hop(_HOP.replace("team=True", "team=False"))
    assert hop is not None and hop["is_team"] is False


def test_the_sentinel_survives_parsing():
    line = _HOP.replace("queue_wait_s=7.97", "queue_wait_s=-1.00").replace(
        "hop_s=31.10", "hop_s=-1.00"
    )
    hop = _parse_hop(line)
    assert hop["queue_wait_ms"] == -1000
    assert hop["hop_ms"] == -1000
    assert hop["turn_ms"] == 23120, "turn never depended on created_at"


def test_a_turn_line_parses():
    turn = _parse_turn(_TURN)
    assert turn is not None
    assert turn["setup_ms"] == 6260
    assert turn["total_ms"] == 23110


def test_unrelated_lines_are_ignored():
    assert _parse_hop("2026-08-14 15:01:03.211 | INFO | something else") is None
    assert _parse_turn(_HOP) is None
    assert _parse_hop(_TURN) is None


# --- the two traps ----------------------------------------------------------

def test_log_timestamps_are_read_as_LOCAL_time():
    """loguru writes local time; the window is built from `datetime.now()`.

    Parsing these as UTC silently discards the most recent hours — on a UTC+8
    box that is the last eight, i.e. exactly the run you just did. The report
    then says "no bus hops in this window" and reads as "the feature is not
    recording".
    """
    ts = _line_time(_HOP)
    assert ts is not None
    assert ts.tzinfo is None, "a naive local stamp, comparable with datetime.now()"
    assert (ts.year, ts.month, ts.day, ts.hour, ts.minute) == (2026, 8, 14, 15, 1)


def test_rotated_zip_logs_are_read_too(tmp_path):
    """Retention is 30 days, but only TODAY is a plain .log — the rest are .zip.

    Globbing `*.log` alone turns a 30-day report into a today-report without
    saying so.
    """
    root = tmp_path / "logs" / "message_bus_trigger"
    root.mkdir(parents=True)
    (root / "bus_20260814.log").write_text(_HOP + "\n", encoding="utf-8")

    zpath = root / "bus_20260813.log.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("bus_20260813.log", _HOP.replace("15:01:03", "15:02:03") + "\n")

    lines = list(_iter_log_lines(tmp_path / "logs"))
    hops = [h for h in (_parse_hop(x) for x in lines) if h]
    assert len(hops) == 2, f"the rotated .zip was skipped: {len(hops)} hop(s)"


def test_a_corrupt_zip_does_not_kill_the_report(tmp_path):
    """One bad archive must cost its own lines, not the whole run."""
    root = tmp_path / "logs" / "svc"
    root.mkdir(parents=True)
    (root / "good.log").write_text(_HOP + "\n", encoding="utf-8")
    (root / "bad.log.zip").write_bytes(b"not a zip at all")

    lines = list(_iter_log_lines(tmp_path / "logs"))
    assert any("[bus-timing]" in ln for ln in lines)


def test_the_window_filter_keeps_recent_and_drops_old(tmp_path):
    root = tmp_path / "logs" / "svc"
    root.mkdir(parents=True)
    now = datetime.now()
    recent = now - timedelta(minutes=5)
    old = now - timedelta(days=3)

    def _stamped(dt: datetime) -> str:
        return _HOP.replace("2026-08-14 15:01:03.211",
                            dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])

    (root / "a.log").write_text(_stamped(recent) + "\n" + _stamped(old) + "\n",
                                encoding="utf-8")

    hops = latency_report._collect(tmp_path / "logs", cutoff=now - timedelta(hours=1),
                                   channel=None)[0]
    assert len(hops) == 1, "the window filter kept the wrong set"
