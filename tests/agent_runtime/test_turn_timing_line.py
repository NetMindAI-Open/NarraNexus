"""
@file_name: test_turn_timing_line.py
@date: 2026-08-05
@description: [turn-timing] — the per-turn phase-split log line.

The line is a grep contract (dashboards / journalctl one-liners will parse
it), so its shape lives in a pure function and this test pins it — same
treatment as [bus-timing] in tests/message_bus/test_bus_hop_timing.py.
A broken format string here would otherwise raise INSIDE run(), after
persistence and before the backgrounded Step-5 hooks: pure observation
code breaking the main path.
"""
from __future__ import annotations

import re

from xyz_agent_context.agent_runtime.agent_runtime import _turn_timing_line

_TIMING_RE = re.compile(
    r"^\[turn-timing\] agent=(?P<agent>\S+) event=(?P<event>\S+) "
    r"source=(?P<source>\S+) pre_s=(?P<pre>\d+\.\d\d) "
    r"setup_s=(?P<setup>\d+\.\d\d) loop_s=(?P<loop>\d+\.\d\d) "
    r"persist_s=(?P<persist>\d+\.\d\d) total_s=(?P<total>\d+\.\d\d) "
    r"interrupted=(?P<interrupted>True|False)$"
)


def test_turn_timing_line_matches_the_grep_contract():
    line = _turn_timing_line(
        agent_id="agent_a", event_id="evt_1", source="chat",
        pre_s=0.128, setup_s=1.5, loop_s=42.0, persist_s=0.4,
        total_s=44.028, interrupted=False,
    )
    m = _TIMING_RE.match(line)
    assert m, line
    assert m["agent"] == "agent_a"
    assert m["event"] == "evt_1"
    assert m["source"] == "chat"
    assert m["pre"] == "0.13"
    assert m["loop"] == "42.00"
    assert m["interrupted"] == "False"


def test_turn_timing_line_interrupted_and_missing_event():
    line = _turn_timing_line(
        agent_id="a", event_id="-", source="message_bus",
        pre_s=0.0, setup_s=0.0, loop_s=0.0, persist_s=0.0,
        total_s=0.0, interrupted=True,
    )
    m = _TIMING_RE.match(line)
    assert m, line
    assert m["event"] == "-"
    assert m["interrupted"] == "True"
