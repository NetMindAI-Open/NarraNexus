"""
latency_report — percentiles for the bus hop and the turn phases, from the logs.

Usage:
    uv run python scripts/diag_collector/latency_report.py [--hours 24]
                                                          [--channel ch_xxx]
                                                          [--logs DIR]

This is the evidence half of PRD "Team chat responsiveness" acceptance #1,
which asks to
take the bus hop "from 45-95s to under 30s, with measured before/after data".

Why it reads LOGS and not a table
=================================
`[bus-timing]` and `[turn-timing]` have carried these numbers since 2026-08-05.
An earlier version of this work mirrored them into two purpose-built tables; that
was dropped, because this project already retains logs properly — loguru writes
per-service files with ``rotation="00:00"`` and ``retention="30 days"``. The
generic "logs rotate away, use the DB" lesson is about `docker logs`, and does
not apply here.

What the tables did buy was a JOIN (hop -> turn -> routing decision on
`event_id`) and self-describing SQL. What they cost was two tables, two
repositories, two writes on the turn's critical path and a migration. For a
question asked during one piece of latency work, that is the wrong trade. If
this becomes a standing concern, the thing to build is a `turn_timing` table —
the setup phase is where the seconds are — not this whole layer.

What the numbers mean
=====================
`queue_wait` is the column acceptance #1 is judged on: message insert -> the
dispatch that picked it up, which already includes the adaptive poll interval,
the wait for a worker slot, and the per-agent lock. That is exactly
"message -> trigger -> start of processing". It does NOT include the turn —
read `turn` for that,
and `hop` for the whole round trip including delivery.

Rows with a `-1` sentinel are EXCLUDED from every percentile and counted
separately: a message that arrived without a `created_at` has no knowable queue
wait, and averaging it in as zero reports the fastest hops we never had.

Percentiles are nearest-rank — no interpolation. At these sample sizes an
interpolated p90 names a latency nobody experienced.
"""

import argparse
import math
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

DEFAULT_LOG_ROOT = Path.home() / ".narranexus" / "logs"

# loguru's default file format starts every record with a LOCAL timestamp.
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[.,](\d{3})")

_HOP_RE = re.compile(
    r"\[bus-timing\] agent=(?P<agent>\S+) channel=(?P<channel>\S+) "
    r"team=(?P<team>\S+) batch=(?P<batch>\d+) "
    r"queue_wait_s=(?P<queue>-?\d+\.\d+) oldest_wait_s=(?P<oldest>-?\d+\.\d+) "
    r"turn_s=(?P<turn>-?\d+\.\d+) hop_s=(?P<hop>-?\d+\.\d+)"
)

_TURN_RE = re.compile(
    r"\[turn-timing\] agent=(?P<agent>\S+) event=(?P<event>\S+) "
    r"source=(?P<source>\S+) "
    r"pre_s=(?P<pre>\d+\.\d+) setup_s=(?P<setup>\d+\.\d+) "
    r"loop_s=(?P<loop>\d+\.\d+) persist_s=(?P<persist>\d+\.\d+) "
    r"total_s=(?P<total>\d+\.\d+) interrupted=(?P<interrupted>\w+)"
)


def _ms(seconds: str) -> int:
    """Seconds as printed -> integer milliseconds, sentinel preserved.

    `-1.00` becomes `-1000` rather than being rounded to something plausible:
    downstream, anything negative is dropped from the percentiles, and a value
    that could be mistaken for a real measurement defeats that.
    """
    return int(round(float(seconds) * 1000))


def _line_time(line: str) -> Optional[datetime]:
    """The record's timestamp, as NAIVE LOCAL time.

    Naive on purpose: loguru writes local time, and the window is built from
    `datetime.now()`. Attaching UTC here would silently drop the most recent
    hours — on a UTC+8 box, the whole working day — and the report would print
    "no bus hops in this window", which reads as "the feature is not recording".
    """
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(
            f"{m.group(1)}.{m.group(2)}", "%Y-%m-%d %H:%M:%S.%f"
        )
    except ValueError:
        return None


def _iter_log_lines(root: Path) -> Iterator[str]:
    """Every line under `root`, including rotated `.log.zip` archives.

    Retention is 30 days but only TODAY is a plain `.log` — everything older is
    zipped. Globbing `*.log` alone turns a 30-day report into a today-report
    without saying so.

    A single unreadable archive costs its own lines and nothing else: a
    diagnostic that dies on one bad file is worse than one that reports on the
    rest.
    """
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        try:
            if path.suffix == ".zip":
                with zipfile.ZipFile(path) as z:
                    for name in z.namelist():
                        with z.open(name) as fh:
                            for raw in fh:
                                yield raw.decode("utf-8", errors="replace")
            elif path.suffix == ".log":
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    yield from fh
        except (zipfile.BadZipFile, OSError) as e:  # noqa: PERF203
            print(f"  ! skipped {path.name}: {type(e).__name__}: {e}", file=sys.stderr)


def _parse_hop(line: str) -> Optional[Dict]:
    m = _HOP_RE.search(line)
    if not m:
        return None
    return {
        "at": _line_time(line),
        "agent_id": m.group("agent"),
        "channel_id": m.group("channel"),
        "is_team": m.group("team") == "True",
        "batch_size": int(m.group("batch")),
        "queue_wait_ms": _ms(m.group("queue")),
        "oldest_wait_ms": _ms(m.group("oldest")),
        "turn_ms": _ms(m.group("turn")),
        "hop_ms": _ms(m.group("hop")),
    }


def _parse_turn(line: str) -> Optional[Dict]:
    m = _TURN_RE.search(line)
    if not m:
        return None
    return {
        "at": _line_time(line),
        "agent_id": m.group("agent"),
        "event_id": m.group("event"),
        "source": m.group("source"),
        "pre_ms": _ms(m.group("pre")),
        "setup_ms": _ms(m.group("setup")),
        "loop_ms": _ms(m.group("loop")),
        "persist_ms": _ms(m.group("persist")),
        "total_ms": _ms(m.group("total")),
        "interrupted": m.group("interrupted") == "True",
    }


def _collect(
    root: Path, *, cutoff: datetime, channel: Optional[str]
) -> Tuple[List[Dict], List[Dict]]:
    hops: List[Dict] = []
    turns: List[Dict] = []
    for line in _iter_log_lines(root):
        if "[bus-timing]" in line:
            hop = _parse_hop(line)
            if hop and hop["at"] and hop["at"] >= cutoff:
                if channel is None or hop["channel_id"] == channel:
                    hops.append(hop)
        elif "[turn-timing]" in line:
            turn = _parse_turn(line)
            if turn and turn["at"] and turn["at"] >= cutoff:
                turns.append(turn)
    return hops, turns


def _percentile(sorted_values: Sequence[int], pct: float) -> Optional[int]:
    """Nearest-rank percentile: rank = ceil(pct * N), value = sample[rank - 1].

    `sorted_values` must already be sorted. The result is always one of the
    samples, which is the property that matters here — an interpolated p90 at
    these sample sizes names a latency no hop ever had.
    """
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "     —"
    return f"{ms / 1000.0:6.2f}s"


def _summarise(name: str, values: List[int], dropped: int) -> str:
    usable = sorted(v for v in values if v >= 0)
    line = (
        f"  {name:<12} n={len(usable):<6} "
        f"p50={_fmt_ms(_percentile(usable, 0.50))} "
        f"p90={_fmt_ms(_percentile(usable, 0.90))} "
        f"p99={_fmt_ms(_percentile(usable, 0.99))} "
        f"max={_fmt_ms(usable[-1] if usable else None)}"
    )
    if dropped:
        line += f"   (+{dropped} unmeasurable, excluded)"
    return line


def _report(hours: int, channel: Optional[str], root: Path) -> int:
    cutoff = datetime.now() - timedelta(hours=hours)
    hops, turns = _collect(root, cutoff=cutoff, channel=channel)

    print(f"\nLogs:   {root}")
    print(f"Window: since {cutoff:%Y-%m-%d %H:%M:%S} (local)"
          + (f"  channel={channel}" if channel else ""))
    print(f"Samples: {len(hops)} bus hops, {len(turns)} turns\n")

    if hops:
        team = [h for h in hops if h["is_team"]]
        dm = [h for h in hops if not h["is_team"]]
        for label, rows in (("TEAM ROOMS", team), ("DIRECT", dm)):
            if not rows:
                continue
            print(f"{label}  ({len(rows)} hops)")
            for col, name in (
                ("queue_wait_ms", "queue_wait"),   # <- acceptance #1
                ("oldest_wait_ms", "oldest_wait"),
                ("turn_ms", "turn"),
                ("hop_ms", "hop"),
            ):
                vals = [r[col] for r in rows]
                print(_summarise(name, vals, sum(1 for v in vals if v < 0)))
            print()

        team_queue = sorted(r["queue_wait_ms"] for r in team if r["queue_wait_ms"] >= 0)
        if team_queue:
            p50 = _percentile(team_queue, 0.50)
            p90 = _percentile(team_queue, 0.90)
            verdict = "PASS" if (p90 or 0) < 30_000 else "FAIL"
            print(
                f"Acceptance #1 (team hop: message -> start of processing < 30s):"
                f"  p50={_fmt_ms(p50)}  p90={_fmt_ms(p90)}  -> {verdict}\n"
            )
    else:
        print("No bus hops in this window.\n")

    if turns:
        print(f"TURN PHASES  ({len(turns)} turns)")
        for col, name in (
            ("pre_ms", "pre"),
            ("setup_ms", "setup"),
            ("loop_ms", "loop"),
            ("persist_ms", "persist"),
            ("total_ms", "total"),
        ):
            print(_summarise(name, [t[col] for t in turns], 0))
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="look back N hours (default 24)")
    parser.add_argument("--channel", type=str, default=None, help="restrict hops to one channel_id")
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOG_ROOT,
                        help=f"log root (default {DEFAULT_LOG_ROOT})")
    args = parser.parse_args()
    return _report(args.hours, args.channel, args.logs)


if __name__ == "__main__":
    sys.exit(main())
