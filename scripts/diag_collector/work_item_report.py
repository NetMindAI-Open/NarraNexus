"""
work_item_report — hand-off closure and stall rates, from the logs.

Usage:
    uv run python scripts/diag_collector/work_item_report.py [--hours 168]
                                                            [--team t_xxx]
                                                            [--logs DIR]

This is the measurement PR #230's standing position asks for before anything
stronger than a notice is built: "how many hand-offs never come back" was not a
number anyone could produce, so "do we need platform-side delivery fallback"
had no evidence either way. `make latency-report` measures how FAST a hop is;
nothing measured whether it CLOSED.

Why it reads LOGS and not a table
=================================
Settled during the latency work and re-applied here rather than re-litigated:
loguru already writes per-service files with ``rotation="00:00"`` and
``retention="30 days"``, so thirty days of these lines are on disk. The generic
"logs rotate away, use the DB" lesson is about `docker logs`.

Two purpose-built tables would buy a JOIN and self-describing SQL, and cost two
repositories, writes on the delivery path and a migration — for a question
asked while deciding the size of one feature. The board itself is already a
table; what is NOT in it is HISTORY (an item that opened and closed leaves one
row in its final state), and history is exactly what a rate needs.

What the numbers mean
=====================
* ``opened`` / ``closed`` count ERRAND (auto) items unless --all-origins:
  a tool-made item is a task spanning several errands, so mixing them makes
  "closure rate" mean two things at once.
* ``closure rate`` = closed / opened over the window. It is deliberately NOT
  computed per item — an item opened before the window and closed inside it
  has no `open` line to pair with, and pairing across a truncated window is
  how a rate quietly exceeds 100%. Unpaired closes are reported separately.
* ``stall rate`` = stalled / opened. A stall is logged on the TRANSITION, so
  one dead hand-off counts once no matter how many patrol cycles re-derive it.
* ``time to close`` pairs open→close by item id WITHIN the window only, and is
  therefore biased towards fast closes at the window edge. Reported with its
  sample size for that reason.
"""

import argparse
import math
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence

DEFAULT_LOG_ROOT = Path.home() / ".narranexus" / "logs"

# loguru's default file format starts every record with a LOCAL timestamp.
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[.,](\d{3})")

_ITEM_RE = re.compile(
    r"\[work-item\] action=(?P<action>\w+) item=(?P<item>\S+) "
    r"team=(?P<team>\S+) channel=(?P<channel>\S+) "
    r"assignee=(?P<assignee>\S+) (?:from=(?P<from_agent>\S+) )?"
    r"origin=(?P<origin>\w+)"
)


def _line_time(line: str) -> Optional[datetime]:
    """The record's timestamp, as NAIVE LOCAL time.

    Naive on purpose: loguru writes local time and the window comes from
    `datetime.now()`. Parsing as UTC silently drops the most recent hours — on
    a UTC+8 box the whole working day — and the report then prints "no work
    items in this window", which reads as "the feature is not recording".
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

    Only today is a plain `.log`; everything older is zipped. Globbing `*.log`
    alone turns a 30-day report into a today-report without saying so. One
    unreadable archive costs its own lines and nothing else.
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


def parse_event(line: str) -> Optional[Dict]:
    """One `[work-item]` record, or None. Exposed for the tests."""
    m = _ITEM_RE.search(line)
    if not m:
        return None
    return {
        "at": _line_time(line),
        "action": m.group("action"),
        "item_id": m.group("item"),
        "team_id": m.group("team"),
        "channel_id": m.group("channel"),
        "assignee_id": m.group("assignee"),
        "from_agent": m.group("from_agent") or "",
        "origin": m.group("origin"),
    }


def collect(
    root: Path,
    *,
    cutoff: datetime,
    team: Optional[str],
    origins: Optional[Sequence[str]],
) -> List[Dict]:
    events: List[Dict] = []
    for line in _iter_log_lines(root):
        if "[work-item]" not in line:
            continue
        ev = parse_event(line)
        if not ev or not ev["at"] or ev["at"] < cutoff:
            continue
        if team is not None and ev["team_id"] != team:
            continue
        if origins is not None and ev["origin"] not in origins:
            continue
        events.append(ev)
    return events


def _percentile(sorted_values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank: the result is always one of the samples.

    At these sample sizes an interpolated p90 names a duration nothing ever
    took.
    """
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def summarise(events: Sequence[Dict]) -> Dict:
    """Counts, rates and close-time percentiles for one window."""
    opened = {e["item_id"]: e for e in events if e["action"] == "open"}
    closed = {e["item_id"]: e for e in events if e["action"] == "close"}
    stalled = {e["item_id"] for e in events if e["action"] == "stall"}

    # Only items whose OPEN is also in the window can be paired. Pairing across
    # the edge is how a closure rate silently exceeds 100%.
    paired = [
        (closed[i]["at"] - opened[i]["at"]).total_seconds()
        for i in opened
        if i in closed and closed[i]["at"] and opened[i]["at"]
    ]
    paired.sort()

    n_open = len(opened)
    return {
        "opened": n_open,
        "closed_in_window": len(closed),
        "closed_of_opened": len([i for i in opened if i in closed]),
        "closed_without_open_in_window": len([i for i in closed if i not in opened]),
        "stalled": len(stalled & set(opened)) if n_open else len(stalled),
        "still_open": len([i for i in opened if i not in closed]),
        "closure_rate": (len([i for i in opened if i in closed]) / n_open) if n_open else None,
        "stall_rate": (len(stalled & set(opened)) / n_open) if n_open else None,
        "close_p50_s": _percentile(paired, 0.50),
        "close_p90_s": _percentile(paired, 0.90),
        "close_samples": len(paired),
    }


def _fmt_rate(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _fmt_s(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f}s"


def render(summary: Dict, *, hours: int, team: Optional[str], origins: str) -> str:
    lines = [
        "",
        f"Work-item closure — last {hours}h"
        + (f" · team={team}" if team else "")
        + f" · origin={origins}",
        "=" * 60,
    ]
    if not summary["opened"] and not summary["closed_in_window"]:
        lines += [
            "  no [work-item] records in this window.",
            "  If hand-offs ARE happening, check that the window covers them —",
            "  log timestamps are local time, and only today's file is unzipped.",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"  opened            {summary['opened']}",
        f"  closed (of those) {summary['closed_of_opened']}",
        f"  still open        {summary['still_open']}",
        f"  stalled at least once {summary['stalled']}",
        "",
        f"  closure rate      {_fmt_rate(summary['closure_rate'])}",
        f"  stall rate        {_fmt_rate(summary['stall_rate'])}",
        "",
        f"  time to close     p50 {_fmt_s(summary['close_p50_s'])}"
        f"  p90 {_fmt_s(summary['close_p90_s'])}"
        f"  (n={summary['close_samples']})",
    ]
    if summary["closed_without_open_in_window"]:
        lines.append(
            f"  ({summary['closed_without_open_in_window']} closes had no open "
            f"in this window — excluded from every rate above)"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=168)
    ap.add_argument("--team", default=None)
    ap.add_argument("--logs", type=Path, default=DEFAULT_LOG_ROOT)
    ap.add_argument(
        "--all-origins",
        action="store_true",
        help="include tool-made TASK items, not just auto ERRANDS",
    )
    args = ap.parse_args(argv)

    origins = None if args.all_origins else ("auto",)
    cutoff = datetime.now() - timedelta(hours=args.hours)
    events = collect(args.logs, cutoff=cutoff, team=args.team, origins=origins)
    print(render(
        summarise(events),
        hours=args.hours,
        team=args.team,
        origins="all" if args.all_origins else "auto",
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
