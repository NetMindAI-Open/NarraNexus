"""
@file_name: programmatic.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Hard-metric extraction from a Transcript

Pure functions, no LLM, no I/O beyond reading the transcript JSON. The
output schema is stable across versions; downstream tooling (trend,
dashboards) reads this directly.

Per the README's "three contracts":
  - binary signals only live here (timeout exceeded, error present,
    no-response placeholder present)
  - judgement lives in semantic.py
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

NO_REPLY_PLACEHOLDER = "(Agent decided no response needed)"

# matches "model=<vendor>/<name>" and "model=<vendor>:<name>" in backend log
_MODEL_RE = re.compile(r"model=([A-Za-z0-9_\-./:]+)")


@dataclass
class TurnMetrics:
    turn_index: int
    completed: bool
    timed_out: bool
    transport_error: str | None
    duration_seconds: float | None
    time_to_first_delta_seconds: float | None
    time_to_first_tool_call_seconds: float | None
    reply_chars: int
    no_reply_placeholder_present: bool
    error_event_count: int
    fatal_error_event_count: int
    tool_call_count: int
    tool_names: list[str]
    expect_contains_missing: list[str]
    expect_not_contains_violations: list[str]


@dataclass
class CaseMetrics:
    case_id: str
    pillar: str
    severity: str
    completed_turns: int
    total_turns: int
    overall_duration_seconds: float | None
    backend_log_lines: int
    models_seen: list[str]
    any_no_reply_placeholder: bool
    any_fatal_error: bool
    any_timeout: bool
    any_transport_error: bool
    expect_contains_missing_total: int
    expect_not_contains_violations_total: int
    turns: list[TurnMetrics] = field(default_factory=list)
    binary_pass: bool = False
    binary_pass_reason: str = ""


def analyze_transcript(transcript: dict[str, Any], backend_log: list[str]) -> CaseMetrics:
    turn_metrics: list[TurnMetrics] = []
    any_no_reply = False
    any_fatal = False
    any_timeout = False
    any_transport_error = False
    expect_in_missing_total = 0
    expect_not_in_violations_total = 0

    for turn in transcript.get("turns", []):
        m = _analyze_turn(turn)
        turn_metrics.append(m)
        any_no_reply |= m.no_reply_placeholder_present
        any_fatal |= m.fatal_error_event_count > 0
        any_timeout |= m.timed_out
        any_transport_error |= bool(m.transport_error)
        expect_in_missing_total += len(m.expect_contains_missing)
        expect_not_in_violations_total += len(m.expect_not_contains_violations)

    overall_duration: float | None = None
    if transcript.get("started_at") and transcript.get("ended_at"):
        overall_duration = float(transcript["ended_at"]) - float(transcript["started_at"])

    models_seen = _models_in_log(backend_log)

    binary_pass, reason = _binary_verdict(
        transcript=transcript,
        turn_metrics=turn_metrics,
        any_no_reply=any_no_reply,
        any_fatal=any_fatal,
        any_timeout=any_timeout,
        any_transport_error=any_transport_error,
        expect_in_missing_total=expect_in_missing_total,
        expect_not_in_violations_total=expect_not_in_violations_total,
    )

    return CaseMetrics(
        case_id=transcript["case_id"],
        pillar=transcript["pillar"],
        severity=transcript["severity"],
        completed_turns=sum(1 for t in turn_metrics if t.completed),
        total_turns=len(turn_metrics),
        overall_duration_seconds=overall_duration,
        backend_log_lines=len(backend_log),
        models_seen=models_seen,
        any_no_reply_placeholder=any_no_reply,
        any_fatal_error=any_fatal,
        any_timeout=any_timeout,
        any_transport_error=any_transport_error,
        expect_contains_missing_total=expect_in_missing_total,
        expect_not_contains_violations_total=expect_not_in_violations_total,
        turns=turn_metrics,
        binary_pass=binary_pass,
        binary_pass_reason=reason,
    )


def _analyze_turn(turn: dict[str, Any]) -> TurnMetrics:
    events = turn.get("events", [])
    started_at = float(turn.get("started_at") or 0.0)
    first_delta_ts: float | None = None
    first_tool_call_ts: float | None = None
    tool_names: list[str] = []
    error_count = 0
    fatal_count = 0

    # Tool calls are emitted as `progress` events with details.tool_name
    # rather than the typed `tool_call` message. Count each tool call
    # once at the `running` edge so a single tool invocation is not
    # double counted with its `completed` echo.
    for evt in events:
        ts = evt.get("timestamp")
        et = evt.get("type")
        if et == "agent_response" and first_delta_ts is None and isinstance(ts, (int, float)):
            first_delta_ts = float(ts)
        elif et == "progress" and evt.get("status") == "running":
            details = evt.get("details") or {}
            tname = details.get("tool_name")
            if tname:
                tool_names.append(str(tname))
                if first_tool_call_ts is None and isinstance(ts, (int, float)):
                    first_tool_call_ts = float(ts)
        elif et == "tool_call":
            # legacy / typed path, kept for forward compatibility
            tool_names.append(str(evt.get("tool_name", "")))
            if first_tool_call_ts is None and isinstance(ts, (int, float)):
                first_tool_call_ts = float(ts)
        elif et == "error":
            error_count += 1
            if evt.get("severity") == "fatal":
                fatal_count += 1

    reply = turn.get("final_reply", "") or ""

    expect_in = turn.get("expect_contains", []) or []
    expect_not_in = turn.get("expect_not_contains", []) or []
    expect_in_missing = [s for s in expect_in if s not in reply]
    expect_not_in_violations = [s for s in expect_not_in if s in reply]

    return TurnMetrics(
        turn_index=int(turn.get("turn_index", 0)),
        completed=bool(turn.get("completed")),
        timed_out=bool(turn.get("timed_out")),
        transport_error=turn.get("transport_error"),
        duration_seconds=turn.get("duration_seconds"),
        time_to_first_delta_seconds=(
            first_delta_ts - started_at if first_delta_ts and started_at else None
        ),
        time_to_first_tool_call_seconds=(
            first_tool_call_ts - started_at if first_tool_call_ts and started_at else None
        ),
        reply_chars=len(reply),
        no_reply_placeholder_present=NO_REPLY_PLACEHOLDER in reply,
        error_event_count=error_count,
        fatal_error_event_count=fatal_count,
        tool_call_count=len(tool_names),
        tool_names=tool_names,
        expect_contains_missing=expect_in_missing,
        expect_not_contains_violations=expect_not_in_violations,
    )


def _models_in_log(backend_log: list[str]) -> list[str]:
    """Pull every `model=...` token observed in the log slice."""
    seen: dict[str, None] = {}
    for line in backend_log:
        for m in _MODEL_RE.findall(line):
            if m not in seen:
                seen[m] = None
    return list(seen.keys())


def _binary_verdict(
    *,
    transcript: dict[str, Any],
    turn_metrics: list[TurnMetrics],
    any_no_reply: bool,
    any_fatal: bool,
    any_timeout: bool,
    any_transport_error: bool,
    expect_in_missing_total: int,
    expect_not_in_violations_total: int,
) -> tuple[bool, str]:
    """The hard programmatic verdict.

    Per the README contract, this is binary, deterministic, and never
    consults an LLM. Anything subtler — coherence, intent match — is
    delegated to the semantic phase.
    """
    if transcript.get("driver_error"):
        return False, f"driver_error: {transcript['driver_error']}"
    if any_transport_error:
        return False, "ws transport error on at least one turn"
    if any_timeout:
        return False, "at least one turn exceeded its timeout"
    if any_fatal:
        return False, "fatal error event observed"
    if any_no_reply:
        return False, f"reply contains {NO_REPLY_PLACEHOLDER!r}"
    if expect_in_missing_total:
        return False, f"{expect_in_missing_total} expect_contains string(s) missing"
    if expect_not_in_violations_total:
        return False, f"{expect_not_in_violations_total} expect_not_contains violation(s)"
    if not turn_metrics:
        return False, "no turns ran"
    if not all(t.completed for t in turn_metrics):
        return False, "at least one turn did not complete cleanly"
    # Empty user-visible reply on a user-driven turn is itself a fail.
    # The placeholder check above catches the explicit `(Agent decided
    # no response needed)` string; this catches the variant where the
    # agent did some tool work but never invoked
    # send_message_to_user_directly at all — the user sees nothing.
    # We check by turn rather than aggregated so the report names the
    # offending turn.
    for tm in turn_metrics:
        if tm.reply_chars == 0:
            return False, f"turn {tm.turn_index} produced no user-visible reply"
    return True, "all programmatic gates passed"


def write_case_metrics(metrics: CaseMetrics, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(metrics), indent=2, ensure_ascii=False))
