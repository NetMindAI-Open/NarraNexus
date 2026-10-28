"""
@file_name: test_run_recorder.py
@author:
@date: 2026-07-31
@description: RunRecorder — the transport-agnostic persistence half of
run observability.

Coverage targets:
  * run_id late-binding from the Step-0 progress message (events row
    flips to running, heartbeat starts, on_run_id fires)
  * event_stream rows for tool_call / thinking_segment (組合 B: whole
    segments only, flushed on type switch)
  * finalize semantics: completed fills final_output only when empty;
    cancelled records the reason; failed records the (redacted) error;
    idempotent; record() becomes a no-op afterwards
  * sweep_stale_runs flips only heartbeat-dead running rows
  * the recording kill switch env
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from narranexus.platform.agent_runtime.run_recorder import (
    RECORDING_DISABLED_ENV,
    RunRecorder,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    first_live_run_id,
    recording_enabled,
    sweep_stale_runs,
)
from narranexus.platform.utils.run_liveness import STATE_RUNNING
from narranexus.platform.utils.timezone import utc_now


def _step0_progress(event_id: str) -> dict:
    return {
        "type": "progress",
        "step": "0",
        "status": "completed",
        "title": "Initialized",
        "details": {"event_id": event_id},
    }


async def _seed_events_row(db, event_id: str, **overrides):
    row = {
        "event_id": event_id,
        "trigger": "lark",
        "trigger_source": "test",
        "agent_id": "agent_test",
        "user_id": "u_test",
        "state": "completed",  # Step-0 default — bind flips to running
        "created_at": "2026-07-31T00:00:00",
        "updated_at": "2026-07-31T00:00:00",
    }
    row.update(overrides)
    await db.insert("events", row)


async def _stop(recorder: RunRecorder):
    """Cancel the heartbeat without going through finalize."""
    if recorder._heartbeat_task and not recorder._heartbeat_task.done():
        recorder._heartbeat_task.cancel()
        try:
            await recorder._heartbeat_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_step0_progress_binds_run_id_and_flips_running(db_client):
    await _seed_events_row(db_client, "evt_rr1")
    seen: list[str] = []

    async def on_run_id(run_id: str) -> None:
        seen.append(run_id)

    rec = RunRecorder(db=db_client, on_run_id=on_run_id)
    await rec.record(_step0_progress("evt_rr1"))

    assert rec.run_id == "evt_rr1"
    assert seen == ["evt_rr1"]
    row = await db_client.get_one("events", {"event_id": "evt_rr1"})
    assert row["state"] == STATE_RUNNING
    assert row["started_at"] is not None
    assert rec._heartbeat_task is not None
    await _stop(rec)


@pytest.mark.asyncio
async def test_segments_are_tier_pure_and_carry_the_tier_in_the_row_kind(db_client):
    """A narration/reasoning switch closes the segment, and the kind records which.

    Without this nothing pins the persistence half of the tier: deleting the
    switch-flush or flipping the kind ternary leaves the whole Python suite and
    the whole vitest suite green, and the only symptom is a mid-run refresh
    replaying narration receded — or a sentence torn at the reconnect, i.e. the
    bug this was built to remove, silently back.
    """
    await _seed_events_row(db_client, "evt_tier")
    buffers: list[tuple[str, bool]] = []
    rec = RunRecorder(
        db=db_client,
        on_thinking_buffer=lambda text, mono=False: buffers.append((text, mono)),
    )
    await rec.record(_step0_progress("evt_tier"))

    # narration (monologue == the whole frame), then provider reasoning
    await rec.record({
        "type": "agent_thinking",
        "thinking_content": "Reading the file now.",
        "monologue": "Reading the file now.",
    })
    assert buffers[-1] == ("Reading the file now.", True)  # mirror carries the tier

    await rec.record({"type": "agent_thinking", "thinking_content": "weighing options"})
    await rec.record({
        "type": "progress", "step": "3.4.1", "title": "🔧 Read",
        "details": {"tool_name": "Read", "arguments": {"file": "x"}},
    })

    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_tier"}),
        key=lambda r: r["seq"],
    )
    assert [r["kind"] for r in rows][-3:] == [
        "thinking_segment_monologue", "thinking_segment", "tool_call",
    ]
    by_kind = {r["kind"]: r["payload"] for r in rows}
    # Unmerged: the switch closed the first segment instead of gluing them.
    assert by_kind["thinking_segment_monologue"] == "Reading the file now."
    assert by_kind["thinking_segment"] == "weighing options"
    await _stop(rec)


@pytest.mark.asyncio
async def test_mixed_frame_is_not_persisted_as_narration(db_client):
    """A frame whose monologue is only a SUBSET is not narration.

    The batcher makes frames tier-pure, so this shape should not occur — but
    this is the copy of the rule whose verdict is written to the database, and
    a wrong one promotes provider scratchpad on every later replay of that run.
    """
    await _seed_events_row(db_client, "evt_mixed")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_mixed"))
    await rec.record({
        "type": "agent_thinking",
        "thinking_content": "CoT preamble. Then I speak.",
        "monologue": "Then I speak.",
    })
    await rec.record({
        "type": "progress", "step": "3.4.1", "title": "🔧 Read",
        "details": {"tool_name": "Read", "arguments": {"file": "x"}},
    })

    rows = await db_client.get("event_stream", {"event_id": "evt_mixed"})
    kinds = [r["kind"] for r in rows]
    assert "thinking_segment" in kinds
    assert "thinking_segment_monologue" not in kinds
    await _stop(rec)


@pytest.mark.asyncio
async def test_thinking_segment_flushes_only_on_type_switch(db_client):
    """組合 B invariant: many thinking events buffer into ONE segment row,
    persisted only when a non-thinking event arrives."""
    await _seed_events_row(db_client, "evt_rr2")
    # The mirror callback takes (text, is_monologue) since 2026-08-30 — the
    # replayed partial has to carry its tier or a mid-run refresh renders it
    # at the wrong one.
    buffers: list[tuple[str, bool]] = []
    rec = RunRecorder(
        db=db_client,
        on_thinking_buffer=lambda text, mono=False: buffers.append((text, mono)),
    )
    await rec.record(_step0_progress("evt_rr2"))

    for chunk in ["hello ", "world"]:
        await rec.record({"type": "agent_thinking", "thinking_content": chunk})
    # Mid-segment: transport mirror sees the growing partial, no rows yet.
    assert buffers[-1] == ("hello world", False)
    rows = await db_client.get("event_stream", {"event_id": "evt_rr2"})
    assert [r for r in rows if r["kind"] == "thinking_segment"] == []

    await rec.record({
        "type": "progress", "step": "3.4.1", "title": "🔧 Read",
        "details": {"tool_name": "Read", "arguments": {"file": "x"}},
    })
    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_rr2"}),
        key=lambda r: r["seq"],
    )
    # progress(step0) row + thinking_segment + tool_call, in order.
    kinds = [r["kind"] for r in rows]
    assert kinds[-2:] == ["thinking_segment", "tool_call"]
    seg = next(r for r in rows if r["kind"] == "thinking_segment")
    assert seg["payload"] == "hello world"
    assert buffers[-1] == ("", False)  # mirror reset on flush

    events_row = await db_client.get_one("events", {"event_id": "evt_rr2"})
    assert events_row["tool_call_count"] == 1
    await _stop(rec)


@pytest.mark.asyncio
async def test_finalize_completed_fills_final_output_only_if_empty(db_client):
    await _seed_events_row(db_client, "evt_rr3")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr3"))
    await rec.record({"type": "agent_response", "delta": "hi "})
    await rec.record({"type": "agent_response", "delta": "there"})
    await rec.finalize(STATE_COMPLETED)

    row = await db_client.get_one("events", {"event_id": "evt_rr3"})
    assert row["state"] == STATE_COMPLETED
    assert row["final_output"] == "hi there"
    assert row["finished_at"] is not None

    # Idempotent + record() is a no-op afterwards.
    await rec.finalize(STATE_FAILED)
    await rec.record({"type": "agent_response", "delta": "late"})
    row = await db_client.get_one("events", {"event_id": "evt_rr3"})
    assert row["state"] == STATE_COMPLETED
    assert row["final_output"] == "hi there"


@pytest.mark.asyncio
async def test_finalize_never_overwrites_existing_final_output(db_client):
    # step_4 persists its own final_output; the recorder must not clobber it.
    await _seed_events_row(db_client, "evt_rr4", final_output="canonical")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr4"))
    await rec.record({"type": "agent_response", "delta": "recorder view"})
    await rec.finalize(STATE_COMPLETED)
    row = await db_client.get_one("events", {"event_id": "evt_rr4"})
    assert row["final_output"] == "canonical"


@pytest.mark.asyncio
async def test_finalize_cancelled_and_failed_record_causes(db_client):
    await _seed_events_row(db_client, "evt_rr5")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr5"))
    await rec.finalize(STATE_CANCELLED, cancel_reason="user pressed stop")
    row = await db_client.get_one("events", {"event_id": "evt_rr5"})
    assert row["state"] == STATE_CANCELLED
    assert row["error_message"] == "user pressed stop"

    await _seed_events_row(db_client, "evt_rr6")
    rec2 = RunRecorder(db=db_client)
    await rec2.record(_step0_progress("evt_rr6"))
    await rec2.finalize(
        STATE_FAILED, error_type="TimeoutError", error_message="read timed out",
    )
    row2 = await db_client.get_one("events", {"event_id": "evt_rr6"})
    assert row2["state"] == STATE_FAILED
    assert "read timed out" in (row2["error_message"] or "")


@pytest.mark.asyncio
async def test_finalize_failed_keeps_output_streamed_before_the_failure(db_client):
    """A failed run that already streamed a reply keeps it in final_output
    next to its error_message; only a cancelled run skips the fallback."""
    await _seed_events_row(db_client, "evt_rr7")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr7"))
    await rec.record({"type": "agent_response", "delta": "partial answer"})
    await rec.finalize(STATE_FAILED, error_type="AuthError", error_message="401")
    row = await db_client.get_one("events", {"event_id": "evt_rr7"})
    assert row["state"] == STATE_FAILED
    assert row["final_output"] == "partial answer"

    await _seed_events_row(db_client, "evt_rr8")
    rec2 = RunRecorder(db=db_client)
    await rec2.record(_step0_progress("evt_rr8"))
    await rec2.record({"type": "agent_response", "delta": "half a sentence"})
    await rec2.finalize(STATE_CANCELLED, cancel_reason="user pressed stop")
    row2 = await db_client.get_one("events", {"event_id": "evt_rr8"})
    assert row2["state"] == STATE_CANCELLED
    assert not (row2["final_output"] or "")


@pytest.mark.asyncio
async def test_fatal_error_event_sets_flags(db_client):
    rec = RunRecorder(db=db_client)
    assert rec.had_fatal_error is False
    await rec.record({
        "type": "error", "severity": "fatal",
        "error_type": "NoProviderConfiguredError",
        "error_message": "No provider configured",
    })
    assert rec.had_fatal_error is True
    assert rec.last_error_type == "NoProviderConfiguredError"
    # recovered severities never void the turn
    rec2 = RunRecorder(db=db_client)
    await rec2.record({
        "type": "error", "severity": "recovered",
        "error_type": "api_error", "error_message": "fallback replied",
    })
    assert rec2.had_fatal_error is False


@pytest.mark.asyncio
async def test_progress_rows_replayable(db_client):
    """Progress frames persist as stream rows so observers can replay
    the pre-loop pipeline phases."""
    await _seed_events_row(db_client, "evt_rr7")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_rr7"))
    await rec.record({
        "type": "progress", "step": "1", "title": "Loading context",
        "details": {},
    })
    rows = sorted(
        await db_client.get("event_stream", {"event_id": "evt_rr7"}),
        key=lambda r: r["seq"],
    )
    assert [r["kind"] for r in rows] == ["progress", "progress"]
    payload = json.loads(rows[1]["payload"])
    assert payload["step"] == "1"
    await _stop(rec)


@pytest.mark.asyncio
async def test_tool_call_stamps_the_run_agent_stage(db_client):
    """A tool_call means the model is running — current_stage must match the
    run-agent progress phase's derived label, not the old ``step.3_agent_loop``
    that disagreed with the progress-derived stage and made
    ``events.current_stage`` flap between two strings for one phase.

    Asserted against the SHARED phase constants (derived the same way the
    recorder derives it), not a hand-copied literal: a rename of
    PHASE_RUN_AGENT_TITLE that forgot to update the recorder would turn this
    red instead of silently reopening the flap bug."""
    from narranexus.platform.schema import (
        PHASE_RUN_AGENT_STEP,
        PHASE_RUN_AGENT_TITLE,
    )

    await _seed_events_row(db_client, "evt_stage")
    rec = RunRecorder(db=db_client)
    await rec.record(_step0_progress("evt_stage"))
    await rec.record({
        "type": "progress", "step": "3.4.1", "title": "🔧 Read",
        "details": {"tool_name": "Read", "arguments": {"file": "x"}},
    })
    row = await db_client.get_one("events", {"event_id": "evt_stage"})
    expected = f"step.{PHASE_RUN_AGENT_STEP}_{PHASE_RUN_AGENT_TITLE}"
    assert row["current_stage"] == expected
    # Pin the concrete value too, so a bug in the derivation rule is caught.
    assert row["current_stage"] == "step.3.4_Run Agent"
    await _stop(rec)


@pytest.mark.asyncio
async def test_sweep_flips_only_heartbeat_dead_rows(db_client):
    fresh = utc_now()
    stale = utc_now() - timedelta(seconds=600)
    await _seed_events_row(
        db_client, "evt_alive", state="running",
        started_at=fresh, last_event_at=fresh,
    )
    await _seed_events_row(
        db_client, "evt_dead", state="running",
        started_at=stale, last_event_at=stale,
    )
    flipped = await sweep_stale_runs(db_client)
    assert flipped == 1
    alive = await db_client.get_one("events", {"event_id": "evt_alive"})
    dead = await db_client.get_one("events", {"event_id": "evt_dead"})
    assert alive["state"] == "running"
    assert dead["state"] == STATE_FAILED
    assert "run lost" in (dead["error_message"] or "")


@pytest.mark.asyncio
async def test_sweep_settles_a_cancel_in_flight_as_cancelled(db_client):
    """A stop that was requested but whose run died before writing its own
    terminal row settles as 'cancelled', not 'failed'.

    The acceptance criterion is explicit that a stop must not be recorded as
    a failure (no retry, no failure alert). Without this branch the outcome
    depends on a race — whether the run's own finalize beat the heartbeat
    going stale — so the run would read as 'failed' intermittently, which is
    the worst kind of green: it passes on a re-run.
    """
    stale = utc_now() - timedelta(seconds=600)
    await _seed_events_row(
        db_client, "evt_stopped", state="running",
        started_at=stale, last_event_at=stale,
        cancel_requested_at=utc_now(),
    )
    await _seed_events_row(
        db_client, "evt_crashed", state="running",
        started_at=stale, last_event_at=stale,
    )

    flipped = await sweep_stale_runs(db_client)

    assert flipped == 2
    stopped = await db_client.get_one("events", {"event_id": "evt_stopped"})
    crashed = await db_client.get_one("events", {"event_id": "evt_crashed"})
    assert stopped["state"] == STATE_CANCELLED
    # A cancellation is not a fault — nothing may look like an error here,
    # or the failure alerting downstream treats a user action as an incident.
    assert not (stopped["error_message"] or "")
    assert crashed["state"] == STATE_FAILED


def test_recording_kill_switch(monkeypatch):
    monkeypatch.delenv(RECORDING_DISABLED_ENV, raising=False)
    assert recording_enabled() is True
    monkeypatch.setenv(RECORDING_DISABLED_ENV, "1")
    assert recording_enabled() is False
    monkeypatch.setenv(RECORDING_DISABLED_ENV, "false")
    assert recording_enabled() is True


# --------------------------------------------------------------------------
# first_live_run_id — the cross-process "is this user busy?" truth source
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_live_run_id_finds_a_live_run_for_the_user(db_client):
    fresh = utc_now()
    await _seed_events_row(
        db_client, "evt_live", state="running",
        started_at=fresh, last_event_at=fresh,
    )
    assert await first_live_run_id(db_client, "u_test") == "evt_live"


@pytest.mark.asyncio
async def test_first_live_run_id_ignores_dead_and_terminal_runs(db_client):
    stale = utc_now() - timedelta(seconds=600)
    await _seed_events_row(
        db_client, "evt_stale", state="running",
        started_at=stale, last_event_at=stale,
    )
    await _seed_events_row(db_client, "evt_done", state=STATE_COMPLETED)
    assert await first_live_run_id(db_client, "u_test") is None


@pytest.mark.asyncio
async def test_first_live_run_id_counts_a_just_started_run(db_client):
    """No heartbeat has fired yet — started_at is the fallback. Reading this
    as idle would reopen the race the guard exists to close."""
    await _seed_events_row(
        db_client, "evt_new", state="running",
        started_at=utc_now(), last_event_at=None,
    )
    assert await first_live_run_id(db_client, "u_test") == "evt_new"


@pytest.mark.asyncio
async def test_first_live_run_id_excludes_the_asking_run(db_client):
    """Step 3's own events row is already 'running' when it asks, so counting
    itself would mean 'always busy'."""
    fresh = utc_now()
    await _seed_events_row(
        db_client, "evt_me", state="running",
        started_at=fresh, last_event_at=fresh,
    )
    assert await first_live_run_id(
        db_client, "u_test", exclude_run_id="evt_me"
    ) is None


@pytest.mark.asyncio
async def test_first_live_run_id_is_scoped_to_the_user(db_client):
    fresh = utc_now()
    await _seed_events_row(
        db_client, "evt_other", state="running", user_id="u_other",
        started_at=fresh, last_event_at=fresh,
    )
    assert await first_live_run_id(db_client, "u_test") is None
    assert await first_live_run_id(db_client, "u_other") == "evt_other"


@pytest.mark.asyncio
async def test_first_live_run_id_raises_on_an_unreadable_db():
    """Deliberate: destructive callers must resolve the ambiguity themselves,
    and they all resolve it as busy."""
    class _Broken:
        async def get(self, *a, **kw):
            raise RuntimeError("pool exhausted")

    with pytest.raises(RuntimeError):
        await first_live_run_id(_Broken(), "u_test")


@pytest.mark.asyncio
async def test_sweep_releases_the_lost_runs_half_open_probe(db_client):
    """A run lost mid-flight never reaches BackgroundRun._finalize, so its
    breaker settlement never happens. The sweep releases a PROBING row for
    that agent (back to PAUSED, same delay) — and leaves a row that is not
    PROBING alone."""
    from datetime import timedelta as _td

    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason

    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("agent_lost", {
        "cb_status": CbStatus.PROBING.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + _td(minutes=4),
        "probe_token": "lost-probe",
        "probe_run_id": "evt_lost_probe",
    })
    await repo.upsert_state("agent_cooling", {
        "cb_status": CbStatus.COOLING.value,
        "consecutive_failure_count": 1,
        "cooldown_until": utc_now() + _td(minutes=1),
    })
    stale = utc_now() - _td(seconds=600)
    await _seed_events_row(db_client, "evt_lost_probe", agent_id="agent_lost",
                           state="running", started_at=stale, last_event_at=stale)
    await _seed_events_row(db_client, "evt_lost_cool", agent_id="agent_cooling",
                           state="running", started_at=stale, last_event_at=stale)

    assert await sweep_stale_runs(db_client) == 2

    probe = await repo.get("agent_lost")
    assert probe.cb_status == CbStatus.PAUSED.value
    assert probe.probe_token is None
    assert probe.consecutive_failure_count == 3
    assert (await repo.get("agent_cooling")).cb_status == CbStatus.COOLING.value


@pytest.mark.asyncio
async def test_sweep_releases_a_lost_probe_despite_an_older_live_run(db_client):
    """#394 review I1/I-1: the claimant (the run bound to the claim) died,
    but the agent also has an older, hours-long run that is still beating.
    The sweep must still release the probe — only the bound run is the
    claimant, so no other run keeps the row PROBING for as long as it runs."""
    from datetime import timedelta as _td

    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason

    repo = AgentCircuitBreakerRepository(db_client)
    claimed = utc_now() - _td(minutes=5)
    await repo.upsert_state("agent_weld", {
        "cb_status": CbStatus.PROBING.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now() + _td(minutes=1),
        "probe_token": "dead-claimant",
        "probe_run_id": "evt_weld_claimant",
    })
    await _seed_events_row(db_client, "evt_weld_old", agent_id="agent_weld",
                           state="running", started_at=utc_now() - _td(hours=3),
                           last_event_at=utc_now())
    await _seed_events_row(db_client, "evt_weld_claimant", agent_id="agent_weld",
                           state="running", started_at=claimed + _td(seconds=1),
                           last_event_at=utc_now() - _td(minutes=3))

    assert await sweep_stale_runs(db_client) == 1
    row = await repo.get("agent_weld")
    assert row.cb_status == CbStatus.PAUSED.value
    assert row.probe_token is None


def test_breaker_and_sweep_share_one_liveness_rule_without_a_cycle():
    """#394 review I4: the breaker's probe-claimant check and the stale sweep
    must use the SAME run_is_live object, and the breaker must get it from
    the leaf utils module — not by importing this module back (that closed
    a loop<->runtime import cycle papered over by two lazy imports)."""
    import inspect

    from narranexus.platform.agent_framework.loop import circuit_breaker
    from narranexus.platform.agent_runtime import run_recorder
    from narranexus.platform.utils import run_liveness

    assert circuit_breaker.run_is_live is run_liveness.run_is_live
    assert run_recorder.run_is_live is run_liveness.run_is_live
    assert "agent_runtime.run_recorder" not in inspect.getsource(circuit_breaker)


def test_the_liveness_rule_has_one_import_path():
    """#394 review I4 (second round): ``utils.run_liveness`` is the ONLY
    module callers import the liveness rule from. ``run_recorder`` and
    ``background_run`` use it but must not re-export it — a second path
    invites the next liveness change to land in the heavy runtime module
    and reopen the loop<->runtime cycle."""
    import re
    from pathlib import Path

    from narranexus.platform.agent_runtime import background_run, run_recorder

    names = {
        "HEARTBEAT_INTERVAL_S", "RUN_STALE_AFTER_S", "STATE_RUNNING",
        "parse_db_utc", "run_is_live",
    }
    assert not names & set(run_recorder.__all__)
    assert not names & set(background_run.__all__)

    root = Path(__file__).resolve().parents[2]
    import_block = re.compile(
        r"from narranexus\.platform\.agent_runtime\.(?:run_recorder|background_run)"
        r"\s+import\s+(\([^)]*\)|[^\n]*)"
    )
    offenders = []
    for base in ("src", "backend", "tests", "scripts"):
        for path in (root / base).rglob("*.py"):
            for match in import_block.finditer(path.read_text(encoding="utf-8")):
                imported = set(re.findall(r"\b\w+\b", match.group(1)))
                if imported & names:
                    offenders.append(f"{path.relative_to(root)}: {sorted(imported & names)}")
    assert offenders == []


@pytest.mark.asyncio
async def test_a_probe_carrying_recorder_binds_its_run_as_the_claimant(db_client):
    """#394 second review I-1: the recorder is where every recorded run
    learns its id, so it is where the claimant names its run. A recorder
    carrying the claim binds on the running flip; an ordinary one binds
    nothing, even for the same agent."""
    from narranexus.platform.agent_framework.loop.circuit_breaker import (
        try_begin_probe,
    )
    from narranexus.platform.repository.agent_circuit_breaker_repository import (
        AgentCircuitBreakerRepository,
    )
    from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason

    repo = AgentCircuitBreakerRepository(db_client)
    await repo.upsert_state("agent_bind", {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": 3,
        "cooldown_until": utc_now(),
    })
    adm = await try_begin_probe("agent_bind", db=db_client)
    assert adm.probe_token

    await _seed_events_row(db_client, "evt_plain", agent_id="agent_bind")
    plain = RunRecorder(db=db_client)
    await plain.record(_step0_progress("evt_plain"))
    await _stop(plain)
    assert (await repo.get("agent_bind")).probe_run_id is None

    await _seed_events_row(db_client, "evt_probe", agent_id="agent_bind")
    rec = RunRecorder(db=db_client, agent_id="agent_bind", probe_token=adm.probe_token)
    await rec.record(_step0_progress("evt_probe"))
    await _stop(rec)
    row = await repo.get("agent_bind")
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_run_id == "evt_probe"
