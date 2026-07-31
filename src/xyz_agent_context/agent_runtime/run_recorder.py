"""
@file_name: run_recorder.py
@author:
@date: 2026-07-31
@description: RunRecorder — the persistence half of run observability.

Run observability is a PLATFORM property, not a chat-WebSocket feature:
any agent run — WS chat, openai-compat, Lark/Slack/Telegram/WeChat,
message-bus team rooms, jobs, A2A — persists the same live trace so any
read-side surface (chat reconnect, team roster, a future dashboard) can
observe it through one contract.

This module is the single source of truth for that contract:

* the run state machine (``events.state``: running → completed /
  cancelled / failed) + the 30s heartbeat that proves liveness
* ``run_is_live`` — the ONE read-side answer to "is this run actually
  alive?" (shared by the agents listing, the WS observe endpoint, and
  the stale-run sweep)
* ``sweep_stale_runs`` — flips running rows whose heartbeat died
  (process killed mid-run without ``finalize``). Heartbeat-based, NOT
  process-based: a run may be alive in a different process/container
  than the one sweeping (the old "backend restarted ⇒ every running row
  is stale" assumption broke the moment trigger runs became recorded).
* ``RunRecorder`` — consumes the normalised event stream of one run and
  writes ``event_stream`` rows (組合 B granularity: whole thinking
  segments, one row per tool_call/tool_output/text_delta/progress) plus
  the ``events`` row lifecycle fields.

``RunRecorder`` is transport-agnostic. ``BackgroundRun`` composes it
with a ``Broadcaster`` (live fan-out + active_runs registry) for the
WS/openai paths; ``InProcessAgentRuntimeClient`` mounts it bare on the
trigger paths, where the read side follows the DB instead (see the
observe endpoint's tail-follow branch in backend/routes/websocket.py).

The recorder is an OBSERVER: every DB write is individually guarded so
a persistence failure can never break the run it is recording
(binding rule #14 — the platform must not be the interruption source).
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.utils.timezone import utc_now

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


# Heartbeat cadence — every N seconds the heartbeat task bumps
# events.last_event_at, even if no stream events fired. Used by the
# stale-run sweep and observability read-sides to distinguish a healthy
# long thinking pass from a dead process.
HEARTBEAT_INTERVAL_S = 30


# Run state machine — string-typed for direct DB column compatibility.
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_CANCELLED, STATE_FAILED})


# An events row stuck at state='running' is only trusted as alive while
# its heartbeat is fresh. After 3 missed beats the run is presumed dead —
# its task died without finalize (process killed mid-run, or the terminal
# DB write failed). Shared by every read-side consumer (agents listing,
# WS observe endpoint, stale sweep) so "is this run actually alive?" has
# ONE answer.
#
# This is a read-side liveness rule only — consumers must never stop or
# mutate a live run based on it. A genuinely long-running agent keeps
# beating and stays live, so long agent_loops remain first-class (铁律 #14).
RUN_STALE_AFTER_S = HEARTBEAT_INTERVAL_S * 3


# Kill switch for the trigger-path recording surface. The WS/openai
# paths (BackgroundRun) predate this switch and are NOT affected — chat
# reconnect depends on their persistence.
RECORDING_DISABLED_ENV = "NARRANEXUS_RUN_RECORDING_DISABLED"


def recording_enabled() -> bool:
    """Whether the trigger-path run recording surface is on (default yes)."""
    return os.environ.get(RECORDING_DISABLED_ENV, "").strip().lower() not in (
        "1", "true", "yes", "on",
    )


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


async def sweep_stale_runs(db: "AsyncDatabaseClient") -> int:
    """Flip running rows whose heartbeat died to 'failed'. Returns the
    number of rows flipped.

    Heartbeat-based on purpose: runs may be alive in OTHER processes
    (trigger containers), so "this process doesn't know the run" proves
    nothing. Only a stale heartbeat (3 missed beats) proves the driving
    task died without writing its terminal row. Callers run this at
    startup AND periodically — a run orphaned mid-flight settles within
    ~RUN_STALE_AFTER_S + one sweep interval, regardless of which process
    restarts when.
    """
    flipped = 0
    try:
        running_rows = await db.get("events", {"state": STATE_RUNNING})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[run-sweep] running-rows lookup failed: {e}")
        return 0
    for row in running_rows or []:
        if run_is_live(row):
            continue
        try:
            await db.update(
                "events",
                {"event_id": row["event_id"]},
                {
                    "state": STATE_FAILED,
                    "error_message": "run lost (process died mid-run)",
                    "finished_at": utc_now(),
                },
            )
            flipped += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[run-sweep] failed to mark stale run {row.get('event_id')!r}: {e}"
            )
    if flipped:
        logger.info(f"[run-sweep] flipped {flipped} stale 'running' rows to 'failed'")
    return flipped


# ============================================================================
# Event normalisation — one wire dialect for every consumer.
# ============================================================================

def normalise_event(event: Any) -> dict:
    """Coerce whatever AgentRuntime.run yields into a uniform dict so
    routing logic can pattern-match without isinstance branches."""
    if isinstance(event, dict):
        return event
    if hasattr(event, "to_dict"):
        try:
            return event.to_dict()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(event, "model_dump"):
        try:
            return event.model_dump(mode="json")
        except Exception:  # noqa: BLE001
            pass
    return {"type": "unknown", "data": str(event)}


def event_to_wire(event: Any) -> dict:
    """JSON-serialisable view for broadcast/persistence — same shape the
    legacy WS code expected."""
    return normalise_event(event)


def try_extract_event_id(event: dict) -> Optional[str]:
    """Inspect a progress message for the event_id minted by Step 0.

    Step 0 yields a ProgressMessage with step="0", status="completed",
    and details={..., "event_id": event.id, ...}. Returns None for any
    other message shape.
    """
    if event.get("type") != "progress":
        return None
    details = event.get("details") or {}
    if not isinstance(details, dict):
        return None
    eid = details.get("event_id")
    return str(eid) if eid else None


def _classify_event(event: dict) -> str:
    """Return one of: thinking / tool_call / tool_output / progress /
    text_delta / error / other."""
    t = event.get("type", "")
    if t == "agent_thinking":
        return "thinking"
    if t == "agent_response":
        return "text_delta"
    if t == "progress":
        # tool_call / tool_output are subtypes of progress in the legacy
        # protocol, distinguished by details payload.
        details = event.get("details") or {}
        if details.get("output") is not None:
            return "tool_output"
        if details.get("tool_name") is not None:
            return "tool_call"
        return "progress"
    if t == "agent_tool_call":
        return "tool_call"
    if t == "agent_tool_output":
        return "tool_output"
    if t == "error":
        return "error"
    return "other"


def _extract_thinking_content(event: dict) -> str:
    return event.get("thinking_content") or ""


def _extract_tool_call_payload(event: dict) -> dict:
    details = event.get("details") or {}
    return {
        "tool_name": details.get("tool_name", ""),
        "arguments": details.get("arguments") or {},
        "step": event.get("step", ""),
        "title": event.get("title", ""),
    }


def _extract_tool_output_payload(event: dict) -> dict:
    details = event.get("details") or {}
    output = details.get("output")
    if isinstance(output, str) and len(output) > 4000:
        # Truncate the persisted copy to bound DB row size. The full
        # output stayed available in the live stream; replay shows the
        # truncated version.
        output = output[:4000] + "... [truncated]"
    return {
        "step": event.get("step", ""),
        "title": event.get("title", ""),
        "output": output,
    }


def _extract_progress_stage(event: dict) -> str:
    """Pick a ``current_stage`` label from a progress message. Falls
    back to the step number when no better label is available."""
    step = event.get("step", "")
    title = event.get("title", "")
    return f"step.{step}_{title}".strip("_.") if step else (title or "")


def _extract_text_delta(event: dict) -> str:
    return event.get("delta") or ""


# ============================================================================
# RunRecorder
# ============================================================================

class RunRecorder:
    """Persist one run's live trace: event_stream rows + events row
    lifecycle. Transport-agnostic — composed by BackgroundRun (WS) and
    mounted bare by the AgentRuntimeClient transports (triggers).

    Lifecycle:
      1. ``record(event)`` per normalised runtime event. The first
         Step-0 progress event late-binds ``run_id`` (events row flips
         to running, heartbeat starts, ``on_run_id`` fires).
      2. ``finalize(state, ...)`` exactly once at the end — flushes the
         in-flight thinking segment, stops the heartbeat, writes the
         terminal events row. Idempotent.

    Optional callbacks (both guarded — the recorder never lets an
    observer break the observed run):
      * ``on_run_id(run_id)`` — transport registration (BackgroundRun
        wires the broadcaster + active_runs + ready_event here).
      * ``on_thinking_buffer(text)`` — mirror of the in-flight thinking
        segment (BackgroundRun hands it to the Broadcaster so
        mid-segment subscribers get the partial).
    """

    def __init__(
        self,
        *,
        db: "AsyncDatabaseClient",
        on_run_id: Optional[Callable[[str], Awaitable[None]]] = None,
        on_thinking_buffer: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.db = db
        self.run_id: Optional[str] = None
        self.state: str = STATE_RUNNING
        self.tool_call_count: int = 0
        self.current_stage: str = ""
        # Set True if a *fatal* ErrorMessage was emitted (e.g. no provider
        # configured). Such a run still ends naturally (generator returns)
        # and lands in STATE_COMPLETED — callers that need "did this turn
        # really deliver?" (funnel, circuit breaker) read this flag.
        self.had_fatal_error: bool = False
        self.last_error_type: Optional[str] = None
        self.last_error_message: Optional[str] = None
        # Accumulated AGENT_RESPONSE deltas — the terminal write fills
        # events.final_output from this only when nothing else did.
        self.final_output_buffer: list[str] = []
        self._on_run_id = on_run_id
        self._on_thinking_buffer = on_thinking_buffer
        # Monotonically increasing seq for event_stream rows. Per-run
        # single-writer, so gap-free by construction; the observe
        # endpoint's tail-follow relies on it being monotonic.
        self._seq: int = 0
        # In-flight thinking text since the last type switch. Flushed to
        # ONE event_stream row when a non-thinking event arrives or the
        # run ends (組合 B row-count bound).
        self._segment: list[str] = []
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ----- recording ----------------------------------------------------

    async def record(self, event: dict) -> None:
        """Route one normalised runtime event into persistence.

        1. Late-bind run_id from the Step-0 progress message.
        2. Thinking extends the in-flight segment; anything else flushes
           it FIRST so row order matches user-visible order.
        3. Persist the event itself + counter/stage bumps.
        """
        if self.state in TERMINAL_STATES:
            return  # late event after finalize — transport may still fan out

        if self.run_id is None:
            new_run_id = try_extract_event_id(event)
            if new_run_id:
                await self._bind_run_id(new_run_id)

        kind = _classify_event(event)

        if kind == "thinking":
            self._append_to_segment(_extract_thinking_content(event))
            return

        await self._flush_segment()

        if kind == "tool_call":
            self.tool_call_count += 1
            await self._write_stream_row("tool_call", _extract_tool_call_payload(event))
            await self._update_events_row({
                "tool_call_count": self.tool_call_count,
                "last_event_at": utc_now(),
                "current_stage": "step.3_agent_loop",
            }, context="tool_call bump")
        elif kind == "tool_output":
            await self._write_stream_row("tool_output", _extract_tool_output_payload(event))
            await self._update_events_row(
                {"last_event_at": utc_now()}, context="tool_output bump",
            )
        elif kind == "progress":
            stage = _extract_progress_stage(event)
            if stage:
                self.current_stage = stage
                await self._update_events_row(
                    {"current_stage": stage, "last_event_at": utc_now()},
                    context="stage bump",
                )
            # Persist progress events as well so observers replay them.
            await self._write_stream_row("progress", event_to_wire(event))
        elif kind == "text_delta":
            delta = _extract_text_delta(event)
            if delta:
                self.final_output_buffer.append(delta)
            await self._write_stream_row("text_delta", delta or "")
        elif kind == "error":
            # A fatal error (default severity) means the turn cannot recover
            # and delivered no genuine reply. recovered / recovered_after_reply
            # DID deliver; recoverable is a transient blip — none of those
            # void the turn.
            if (event.get("severity") or "fatal") == "fatal":
                self.had_fatal_error = True
                self.last_error_type = event.get("error_type")
                self.last_error_message = event.get("error_message")
            await self._write_stream_row("error", event_to_wire(event))
        else:
            # Catch-all — preserve in the stream for full replay fidelity.
            await self._write_stream_row("other", event_to_wire(event))

    async def finalize(
        self,
        state: str,
        *,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        cancel_reason: Optional[str] = None,
    ) -> None:
        """Write the terminal events row + stop the heartbeat. Idempotent.

        ``error_type`` / ``error_message`` let the caller's except-branch
        report a crash the event stream never carried; when absent, the
        last fatal ErrorMessage captured by ``record`` is used.
        """
        if self.state in TERMINAL_STATES:
            return
        self.state = state if state in TERMINAL_STATES else STATE_FAILED
        if error_type:
            self.last_error_type = error_type
        if error_message:
            self.last_error_message = error_message

        with suppress(Exception):
            await self._flush_segment()

        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            # CancelledError is a BaseException since Python 3.8, so
            # suppress(Exception) would not catch it — list it explicitly.
            with suppress(asyncio.CancelledError, Exception):
                await self._heartbeat_task

        # Terminal events row (only if run_id was assigned — if Step 0
        # failed before yielding the event_id there is no row to update,
        # because Step 0 also does the INSERT).
        if not self.run_id:
            return
        finished_at = utc_now()
        updates: dict[str, Any] = {
            "state": self.state,
            "finished_at": finished_at,
            "last_event_at": finished_at,
        }
        if self.state == STATE_CANCELLED:
            updates["error_message"] = cancel_reason or "User cancelled"
        elif self.state == STATE_FAILED:
            # Persist the (redacted) failure cause so the row explains
            # itself. NOTE (deliberate asymmetry): a fatal-completed run
            # (STATE_COMPLETED + had_fatal_error — e.g. dead key/quota that
            # ends the generator naturally) does NOT get error_message:
            # stamping an error on a completed row would surface a spurious
            # failure. The cause is already an `error` stream row.
            from xyz_agent_context.agent_framework.llm.failure import redact_secrets
            if self.last_error_message:
                updates["error_message"] = redact_secrets(self.last_error_message)
        if self.state == STATE_COMPLETED and self.final_output_buffer:
            # AgentRuntime's step_4 also writes final_output from its own
            # bookkeeping; never overwrite a non-empty value.
            with suppress(Exception):
                existing = await self.db.get_one("events", {"event_id": self.run_id})
                if existing and not (existing.get("final_output") or "").strip():
                    updates["final_output"] = "".join(self.final_output_buffer)
        await self._update_events_row(updates, context="terminal write")

    # ----- run_id late-binding ------------------------------------------

    async def _bind_run_id(self, run_id: str) -> None:
        """First Step-0 progress message seen — wire up everything that
        needs run_id: the events-row running flip, the heartbeat task,
        and the caller's transport registration. Idempotent."""
        if self.run_id is not None:
            return
        self.run_id = run_id
        now = utc_now()
        await self._update_events_row(
            {"state": STATE_RUNNING, "started_at": now, "last_event_at": now},
            context="running init",
        )
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._on_run_id is not None:
            try:
                await self._on_run_id(run_id)
            except Exception:  # noqa: BLE001 — observer never breaks observed
                logger.opt(exception=True).warning(
                    f"[RunRecorder {run_id}] on_run_id callback failed"
                )

    # ----- segment helpers ----------------------------------------------

    def _append_to_segment(self, text: str) -> None:
        if not text:
            return
        self._segment.append(text)
        if self._on_thinking_buffer is not None:
            self._on_thinking_buffer("".join(self._segment))

    async def _flush_segment(self) -> None:
        """Persist the accumulated thinking segment as ONE row and reset
        both the local buffer and the transport mirror."""
        if not self._segment:
            return
        segment_text = "".join(self._segment)
        self._segment = []
        if self._on_thinking_buffer is not None:
            self._on_thinking_buffer("")
        await self._write_stream_row("thinking_segment", segment_text)

    # ----- DB write helpers ---------------------------------------------

    async def _write_stream_row(self, kind: str, payload: Any) -> None:
        """Append a row to event_stream. No-op before run_id is bound —
        those very early messages predate the events row, so there is
        nothing to relate them to."""
        if not self.run_id:
            return
        self._seq += 1
        if isinstance(payload, (dict, list)):
            payload_text = json.dumps(payload, ensure_ascii=False)
        else:
            payload_text = str(payload) if payload is not None else ""
        try:
            await self.db.insert(
                "event_stream",
                {
                    "event_id": self.run_id,
                    "seq": self._seq,
                    "kind": kind,
                    "payload": payload_text,
                    "created_at": utc_now(),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[RunRecorder {self.run_id}] event_stream write failed "
                f"(kind={kind!r}, seq={self._seq}): {e}"
            )

    async def _update_events_row(self, updates: dict, *, context: str) -> None:
        """Guarded events-row UPDATE — a persistence failure must never
        break the run being recorded."""
        if not self.run_id:
            return
        try:
            await self.db.update("events", {"event_id": self.run_id}, updates)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RunRecorder {self.run_id}] {context} failed: {e}")

    async def _heartbeat_loop(self) -> None:
        """Bump last_event_at every HEARTBEAT_INTERVAL_S while alive."""
        try:
            while self.state not in TERMINAL_STATES:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if self.state in TERMINAL_STATES:
                    return
                await self._update_events_row(
                    {"last_event_at": utc_now()}, context="heartbeat",
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RunRecorder {self.run_id}] heartbeat loop crashed: {e}")


__all__ = [
    "HEARTBEAT_INTERVAL_S",
    "RECORDING_DISABLED_ENV",
    "RUN_STALE_AFTER_S",
    "RunRecorder",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "STATE_RUNNING",
    "TERMINAL_STATES",
    "event_to_wire",
    "normalise_event",
    "parse_db_utc",
    "recording_enabled",
    "run_is_live",
    "sweep_stale_runs",
    "try_extract_event_id",
]
