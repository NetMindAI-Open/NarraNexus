"""
@file_name: test_work_item_report.py
@date: 2026-08-14
@description: The closure-rate maths behind "is a stronger fallback needed".

PR #230's standing position is that platform-side delivery fallback needs
measurement first. `latency-report` measures how FAST a hop is; nothing
measured whether the hand-off ever CLOSED. This report does, by reading the
`[work-item]` lines the errand layer writes.

The traps pinned here are the ones that make a diagnostic lie quietly rather
than fail loudly:

* a rate must never exceed 100%. An item closed inside the window but opened
  before it has no `open` line to pair with, so counting it as a close of a
  window-opened item inflates the numerator past the denominator.
* a stall is logged on the TRANSITION. Patrol re-derives `stalled` every
  sweep, so a per-sweep line would make one dead hand-off read as hundreds.
* origins are not mixed by default: a `tool` item is a TASK spanning several
  errands, so folding it into "closure rate" makes the number mean two things.
* the same LOCAL-time and `.zip` handling as the latency report — both fail as
  "no data", which reads as "the feature is not recording".
"""
from __future__ import annotations

import importlib.util
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "diag_collector" / "work_item_report.py"
)
_spec = importlib.util.spec_from_file_location("work_item_report", _MODULE_PATH)
work_item_report = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(work_item_report)

parse_event = work_item_report.parse_event
summarise = work_item_report.summarise
collect = work_item_report.collect
render = work_item_report.render


def _line(at: datetime, action: str, item: str, *, origin="auto",
          team="t1", assignee="agent_a") -> str:
    stamp = at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    extra = "from=agent_lead " if action == "open" else ""
    return (
        f"{stamp} | INFO | narranexus.platform.message_bus.errand:x:1 - "
        f"[work-item] action={action} item={item} team={team} channel=ch_1 "
        f"assignee={assignee} {extra}origin={origin}\n"
    )


# --- parsing ----------------------------------------------------------------

def test_both_line_shapes_parse():
    """`open` carries `from=`, `close`/`stall` do not — one regex, both."""
    now = datetime.now()
    opened = parse_event(_line(now, "open", "wi_1"))
    closed = parse_event(_line(now, "close", "wi_1"))

    assert opened["action"] == "open"
    assert opened["from_agent"] == "agent_lead"
    assert opened["origin"] == "auto"
    assert closed["action"] == "close"
    assert closed["from_agent"] == ""
    assert closed["item_id"] == "wi_1"


def test_an_unrelated_line_is_not_an_event():
    assert parse_event("2026-08-14 10:00:00.000 | INFO | something else") is None


# --- the rate, and the ways it could lie ------------------------------------

def test_a_clean_window_reports_the_obvious_numbers():
    now = datetime.now()
    events = [
        parse_event(_line(now - timedelta(minutes=10), "open", "wi_1")),
        parse_event(_line(now - timedelta(minutes=9), "close", "wi_1")),
        parse_event(_line(now - timedelta(minutes=8), "open", "wi_2")),
    ]

    s = summarise(events)

    assert s["opened"] == 2
    assert s["closed_of_opened"] == 1
    assert s["still_open"] == 1
    assert s["closure_rate"] == 0.5
    assert s["close_samples"] == 1
    assert s["close_p50_s"] == 60.0


def test_a_close_without_its_open_cannot_push_the_rate_over_100():
    """The edge case that makes a rate meaningless if it is mishandled."""
    now = datetime.now()
    events = [
        parse_event(_line(now - timedelta(minutes=5), "open", "wi_new")),
        parse_event(_line(now - timedelta(minutes=4), "close", "wi_new")),
        # Opened before the window; only its close is visible here.
        parse_event(_line(now - timedelta(minutes=3), "close", "wi_older")),
    ]

    s = summarise(events)

    assert s["opened"] == 1
    assert s["closure_rate"] == 1.0
    assert s["closed_without_open_in_window"] == 1
    assert s["closure_rate"] <= 1.0


def test_one_dead_hand_off_counts_as_one_stall():
    """Guards the producer's contract as much as the consumer's maths: the
    stall line is written on the transition, so the report must not need to
    dedup — but if the producer ever regresses, this is where it shows."""
    now = datetime.now()
    events = [
        parse_event(_line(now - timedelta(minutes=30), "open", "wi_1")),
        parse_event(_line(now - timedelta(minutes=20), "stall", "wi_1")),
    ]

    s = summarise(events)

    assert s["stalled"] == 1
    assert s["stall_rate"] == 1.0
    assert s["closure_rate"] == 0.0


def test_an_empty_window_says_so_instead_of_printing_a_fake_rate():
    s = summarise([])

    assert s["opened"] == 0
    assert s["closure_rate"] is None
    assert "no [work-item] records" in render(s, hours=24, team=None, origins="auto")


# --- collection: the two quiet failure modes --------------------------------

def test_rotated_zip_archives_are_read_too(tmp_path):
    """Only today is a plain `.log`; reading just those turns a 7-day report
    into a today-report without saying so."""
    now = datetime.now()
    (tmp_path / "bus.log").write_text(_line(now, "open", "wi_today"))
    archive = tmp_path / "bus.2026-08-13.log.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("bus.log", _line(now - timedelta(hours=20), "open", "wi_zipped"))

    events = collect(
        tmp_path, cutoff=now - timedelta(hours=48), team=None, origins=("auto",)
    )

    assert {e["item_id"] for e in events} == {"wi_today", "wi_zipped"}


def test_a_corrupt_archive_costs_only_its_own_lines(tmp_path):
    now = datetime.now()
    (tmp_path / "bus.log").write_text(_line(now, "open", "wi_ok"))
    (tmp_path / "broken.log.zip").write_bytes(b"not a zip at all")

    events = collect(
        tmp_path, cutoff=now - timedelta(hours=1), team=None, origins=("auto",)
    )

    assert [e["item_id"] for e in events] == ["wi_ok"]


def test_the_window_is_local_time_like_the_logs(tmp_path):
    """A UTC cutoff against local stamps drops the most recent hours — on a
    UTC+8 box, the whole working day — and prints "no records"."""
    now = datetime.now()
    (tmp_path / "bus.log").write_text(_line(now - timedelta(minutes=5), "open", "wi_1"))

    assert collect(
        tmp_path, cutoff=now - timedelta(hours=1), team=None, origins=("auto",)
    )
    assert collect(
        tmp_path, cutoff=now + timedelta(hours=1), team=None, origins=("auto",)
    ) == []


def test_tool_items_are_excluded_unless_asked_for(tmp_path):
    """A task is not an errand; averaging them makes "closure rate" ambiguous."""
    now = datetime.now()
    (tmp_path / "bus.log").write_text(
        _line(now, "open", "wi_errand", origin="auto")
        + _line(now, "open", "wi_task", origin="tool")
    )

    auto_only = collect(
        tmp_path, cutoff=now - timedelta(hours=1), team=None, origins=("auto",)
    )
    everything = collect(
        tmp_path, cutoff=now - timedelta(hours=1), team=None, origins=None
    )

    assert [e["item_id"] for e in auto_only] == ["wi_errand"]
    assert len(everything) == 2


def test_team_filter_narrows_to_one_room(tmp_path):
    now = datetime.now()
    (tmp_path / "bus.log").write_text(
        _line(now, "open", "wi_1", team="t1") + _line(now, "open", "wi_2", team="t2")
    )

    events = collect(
        tmp_path, cutoff=now - timedelta(hours=1), team="t2", origins=("auto",)
    )

    assert [e["item_id"] for e in events] == ["wi_2"]
