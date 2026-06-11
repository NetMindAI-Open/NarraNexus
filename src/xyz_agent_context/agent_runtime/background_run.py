"""
@file_name: background_run.py
@author: Bin Liang
@date: 2026-05-13
@description: Decoupled agent run task + persistence + broadcast.

This module is the heart of Phase C of the agent-runtime-lifecycle spec.

Before this module, ``websocket_agent_run`` directly drove
``AgentRuntime.run()`` in the same asyncio task that owned the
WebSocket — so when the WS closed (user closed the tab, network blip,
uvicorn ping timeout), ``_listen_for_stop`` cancelled the token and
the agent died on the spot. That broke iron rule #14 ("agents may
run for hours; the platform does not be the reason a healthy run
gets interrupted").

BackgroundRun lifts agent execution OUT of the WebSocket task into a
self-owned background task. The WebSocket becomes a subscriber to a
``Broadcaster`` that fans the agent's stream out to every connected
browser tab. WS drop → unsubscribe; agent keeps running. New WS
connection with ``?run_id=X`` → replay history from DB + subscribe to
broadcaster (if run still alive).

Persistence model (Phase C 組合 B)
----------------------------------
Three things get written to the database as the run executes:

* ``events`` row state — `running`, `last_event_at` heartbeat every 30s,
  `tool_call_count` incremented on each tool_call, `current_stage`
  bumped on each step transition. Terminal: state flips to
  `completed` / `cancelled` / `failed` with `finished_at` set.
* ``event_stream`` rows — one per tool_call, one per tool_output, one
  per FULL thinking segment (segment = contiguous thinking between
  two type switches). 100ms-coalesced thinking deltas DO NOT each
  produce a row; only the complete segment at switch-time does.
* ``user_notifications`` rows — currently only for force_stop. Hook
  point for future "agent finished while you were away" notifications.

The "thinking segment" boundary is what makes DB row count tractable
even on a 13-min Xiong-style run (4408 raw thinking chunks → ~50
segment rows because the segments are bounded by tool_call switches).

Lifecycle binding (no TTL)
--------------------------
The Broadcaster is destroyed the instant the BackgroundRun task exits.
There is no "keep around for 5 minutes" grace. Reconnects after run
completion read from event_stream + events.final_output — no live
state is needed because there is no live state to consume.

The ``active_runs`` registry on ``app.state`` is the in-memory map of
``run_id → BackgroundRun``. Entries are removed on terminal state.
On backend restart, the registry is empty by definition and any
``events.state = 'running'`` rows are stale — ``reconcile_stale_runs``
in ``backend/main.lifespan`` flips them to `failed`.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.broadcaster import Broadcaster
from xyz_agent_context.agent_runtime.cancellation import (
    CancellationToken,
    CancelledByUser,
)
from xyz_agent_context.utils.timezone import utc_now

if TYPE_CHECKING:
    from xyz_agent_context.utils.database import AsyncDatabaseClient


# Heartbeat cadence — every N seconds the heartbeat task bumps
# events.last_event_at, even if no stream events fired. Used by
# reconcile and admin observability to distinguish a healthy long
# thinking pass from a wedged backend.
HEARTBEAT_INTERVAL_S = 30


# Run state machine — string-typed for direct DB column compatibility.
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_CANCELLED, STATE_FAILED})


# An events row stuck at state='running' is only trusted as alive while
# its heartbeat is fresh. After 3 missed beats the run is presumed dead —
# its task died without _finalize (process killed mid-run, or the terminal
# DB write failed). Shared by every read-side consumer (agents listing,
# WS reconnect) so "is this run actually alive?" has ONE answer.
#
# This is a read-side liveness rule only — consumers must never stop or
# mutate a run based on it. A genuinely long-running agent keeps beating
# and stays live, so long agent_loops remain first-class (铁律 #14).
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


async def _fire_message_success(*, user_id: str, agent_id: str,
                                run_id: "str | None") -> None:
    """Funnel step ⑤: agent produced and delivered a reply (COMPLETED)."""
    if not user_id:
        return
    from xyz_agent_context.analytics import track
    from xyz_agent_context.analytics.events import (
        EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED, PROP_AGENT_ID, PROP_RUN_ID,
    )
    await track(
        user_id=user_id,
        event=EVENT_MESSAGE_ROUND_TRIP_SUCCEEDED,
        properties={PROP_AGENT_ID: agent_id, PROP_RUN_ID: run_id},
    )


class BackgroundRun:
    """One detached agent run.

    A new BackgroundRun is created when a fresh WS connection lands
    on ``/ws/agent/run`` without a ``run_id`` query parameter. The
    caller is expected to:

      1. ``app.state.active_runs[bg.run_id] = bg``
      2. ``bg.task = asyncio.create_task(bg.drive(...))``
      3. subscribe their WebSocket via ``bg.broadcaster.subscribe(...)``

    The BackgroundRun owns:
      * the CancellationToken (any caller can pass ``cancel()`` to stop)
      * the Broadcaster
      * the heartbeat task
      * the persistence side-effects (event_stream + events updates)

    BackgroundRun does NOT own the AgentRuntime instance — caller
    constructs it and passes the async generator into ``drive``.
    """

    def __init__(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_preview: str,
        db: "AsyncDatabaseClient",
        active_runs: dict,
        cancellation: Optional[CancellationToken] = None,
    ) -> None:
        # run_id starts unset; drive() extracts the AgentRuntime-assigned
        # event_id from the first step-0 progress message and only THEN
        # registers in active_runs + signals ready_event. Callers that
        # need a stable id (to subscribe via Broadcaster, or to expose
        # to the WS client) must ``await bg.ready_event.wait()`` first.
        self.run_id: Optional[str] = None
        self.agent_id = agent_id
        self.user_id = user_id
        self.input_preview = input_preview[:200] if input_preview else ""
        self.db = db
        self._active_runs = active_runs  # the app.state.active_runs map
        self.cancellation = cancellation or CancellationToken()
        # Broadcaster is run_id-tagged but only for logging. It works
        # without run_id set.
        self.broadcaster = Broadcaster("<pending>")
        self.state: str = STATE_RUNNING
        self.tool_call_count: int = 0
        self.current_stage: str = ""
        # Monotonically increasing seq used for event_stream rows. Both
        # drive() and any reconnect-time replay code rely on this being
        # gap-free per run.
        self._seq: int = 0
        # current_thinking_segment is the in-flight thinking text since
        # the last type-switch. Flushed to event_stream when a non-
        # thinking event arrives or the run ends. Kept in-process so
        # mid-segment reconnects can hand the partial buffer to the
        # new subscriber via Broadcaster.set_current_thinking_buffer.
        self._current_thinking_segment: list[str] = []
        # Heartbeat task handle — started lazily on drive(), stopped on close.
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._task: Optional[asyncio.Task] = None
        # final_output is captured when we see the chat module emit it.
        # On terminal write, this is what goes into events.final_output.
        self.final_output_buffer: list[str] = []
        # Set True if a *fatal* ErrorMessage was emitted (e.g. no provider
        # configured). The run still ends naturally (generator returns), so
        # STATE_COMPLETED is set — but it produced no genuine reply, so the
        # message_round_trip_succeeded funnel event must NOT fire.
        self._had_fatal_error: bool = False
        # Signals run_id has been assigned + active_runs registration done.
        # Callers gate "subscribe + return run_id to client" on this.
        self.ready_event: asyncio.Event = asyncio.Event()

    @property
    def task(self) -> Optional[asyncio.Task]:
        return self._task

    @task.setter
    def task(self, value: asyncio.Task) -> None:
        self._task = value

    # ----- run_id late-binding ------------------------------------------

    async def _on_run_id_assigned(self, run_id: str) -> None:
        """Called from drive() the first time AgentRuntime emits a
        progress message containing the event_id Step 0 just minted.

        Wires up everything that needs run_id: the broadcaster's
        debug tag, active_runs registration, the heartbeat task, the
        events-row Phase-C-fields UPDATE. Idempotent — guards on
        run_id already being set.
        """
        if self.run_id is not None:
            return
        self.run_id = run_id
        self.broadcaster.run_id = run_id
        self._active_runs[run_id] = self
        logger.info(
            f"[BackgroundRun] run_id={run_id} bound to in-memory registry "
            f"(active_runs now has {len(self._active_runs)} entries)"
        )
        await self._ensure_events_row()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self.ready_event.set()

    # ----- DB write helpers ---------------------------------------------

    async def _ensure_events_row(self) -> None:
        """Update the (already-inserted-by-step-0) events row to record
        Phase C lifecycle fields.

        Step 0 of AgentRuntime is the one that INSERT-s the events row
        and assigns event_id. We learn the event_id by intercepting the
        step-0-completion progress message inside emit() and only THEN
        call this. The UPDATE is idempotent.
        """
        if not self.run_id:
            return  # Should not happen — caller gates on ready_event
        try:
            await self.db.update(
                "events",
                {"event_id": self.run_id},
                {
                    "state": STATE_RUNNING,
                    "started_at": utc_now(),
                    "last_event_at": utc_now(),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[BackgroundRun {self.run_id}] events-row state init failed: {e}")

    async def _bump_heartbeat(self) -> None:
        """Update last_event_at on the events row. Errors swallowed.
        No-op if run_id is not assigned yet."""
        if self.state in TERMINAL_STATES or not self.run_id:
            return
        try:
            await self.db.update(
                "events",
                {"event_id": self.run_id},
                {"last_event_at": utc_now()},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[BackgroundRun {self.run_id}] heartbeat write failed: {e}")

    async def _write_stream_row(self, kind: str, payload: Any) -> None:
        """Append a row to event_stream for this run. ``payload`` is
        serialised to JSON unless it is already a string.

        No-op when run_id is not yet assigned (i.e. before Step 0
        completion). Those very early messages are broadcast-only —
        they predate the events row so there is nothing to relate to.
        """
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
                f"[BackgroundRun {self.run_id}] event_stream write failed "
                f"(kind={kind!r}, seq={self._seq}): {e}"
            )

    async def _heartbeat_loop(self) -> None:
        """Background task — bump last_event_at every HEARTBEAT_INTERVAL_S
        seconds while the run is alive."""
        try:
            while self.state not in TERMINAL_STATES:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if self.state in TERMINAL_STATES:
                    return
                await self._bump_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[BackgroundRun {self.run_id}] heartbeat loop crashed: {e}")

    # ----- segment helpers ----------------------------------------------

    def _append_to_segment(self, text: str) -> None:
        """Accumulate a thinking chunk into the in-flight segment buffer
        and update the broadcaster snapshot so mid-segment reconnects
        get the full partial."""
        if not text:
            return
        self._current_thinking_segment.append(text)
        self.broadcaster.set_current_thinking_buffer(
            "".join(self._current_thinking_segment)
        )

    async def _flush_segment(self) -> None:
        """If a thinking segment has accumulated, persist it as ONE
        event_stream row and reset both the local buffer and the
        broadcaster snapshot."""
        if not self._current_thinking_segment:
            return
        segment_text = "".join(self._current_thinking_segment)
        self._current_thinking_segment = []
        self.broadcaster.set_current_thinking_buffer("")
        await self._write_stream_row("thinking_segment", segment_text)

    # ----- public event sink (called from drive) ------------------------

    async def emit(self, event: dict) -> None:
        """Process one runtime message from agent_runtime.run().

        Routing logic:
          1. Decide if this event ends the current thinking segment
             (any non-thinking-kind event does). If so, flush the
             segment FIRST so the row order matches user-visible order.
          2. Persist the event itself (event_stream + counter updates).
          3. Broadcast a JSON-friendly version to subscribers.
        """
        if self.state in TERMINAL_STATES:
            # Late event — run is already done. Just broadcast (in case
            # a subscriber is still draining) but don't persist.
            self.broadcaster.publish(_event_to_wire(event))
            return

        event_kind = _classify_event(event)

        # Step 1: segment switch handling. A thinking event extends the
        # current segment; anything else terminates it.
        if event_kind == "thinking":
            # The WS-tier ResponseProcessor already coalesced into a
            # ~100ms / ≥500-char chunk. We append that chunk to the DB
            # segment buffer.
            content = _extract_thinking_content(event)
            self._append_to_segment(content)
            # Broadcast immediately so the user sees the typewriter chunk.
            self.broadcaster.publish(_event_to_wire(event))
            return

        # Non-thinking event arriving — terminate the in-flight segment.
        await self._flush_segment()

        # Step 2: per-kind persistence + counter bumps
        if event_kind == "tool_call":
            self.tool_call_count += 1
            tool_payload = _extract_tool_call_payload(event)
            await self._write_stream_row("tool_call", tool_payload)
            try:
                await self.db.update(
                    "events",
                    {"event_id": self.run_id},
                    {
                        "tool_call_count": self.tool_call_count,
                        "last_event_at": utc_now(),
                        "current_stage": "step.3_agent_loop",
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[BackgroundRun {self.run_id}] events row bump on tool_call failed: {e}")
        elif event_kind == "tool_output":
            output_payload = _extract_tool_output_payload(event)
            await self._write_stream_row("tool_output", output_payload)
            try:
                await self.db.update(
                    "events",
                    {"event_id": self.run_id},
                    {"last_event_at": utc_now()},
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[BackgroundRun {self.run_id}] events row bump on tool_output failed: {e}")
        elif event_kind == "progress":
            # Progress messages map onto stage transitions — record current_stage.
            stage = _extract_progress_stage(event)
            if stage:
                self.current_stage = stage
                try:
                    await self.db.update(
                        "events",
                        {"event_id": self.run_id},
                        {"current_stage": stage, "last_event_at": utc_now()},
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[BackgroundRun {self.run_id}] stage bump failed: {e}")
            # Persist progress events as well so reconnect replays them.
            await self._write_stream_row("progress", _event_to_wire(event))
        elif event_kind == "text_delta":
            # User-visible reply text. Accumulate for events.final_output
            # at terminal, persist each delta as a stream row so reconnect
            # can replay the typewriter.
            delta = _extract_text_delta(event)
            if delta:
                self.final_output_buffer.append(delta)
            await self._write_stream_row("text_delta", delta or "")
        elif event_kind == "error":
            # A fatal error (default severity) means the turn cannot recover
            # and delivered no genuine reply. recovered / recovered_after_reply
            # DID deliver a reply; recoverable is a transient blip the loop
            # survives — none of those should void a successful round-trip.
            if (event.get("severity") or "fatal") == "fatal":
                self._had_fatal_error = True
            await self._write_stream_row("error", _event_to_wire(event))
        else:
            # Catch-all — preserve in the stream for full replay fidelity.
            await self._write_stream_row("other", _event_to_wire(event))

        # Step 3: broadcast to live subscribers
        self.broadcaster.publish(_event_to_wire(event))

    # ----- driver entrypoint --------------------------------------------

    async def drive(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any,
        pass_mcp_urls: Optional[dict] = None,
        trigger_extra_data: Optional[dict] = None,
    ) -> None:
        """Own AgentRuntime lifecycle + consume run() to completion +
        persist everything + broadcast.

        This is the task body that ``asyncio.create_task(bg.drive(...))``
        invokes. Caller does NOT pass a runtime in — BackgroundRun manages
        AgentRuntime entirely so the run continues to live even after the
        WebSocket that started it disconnects.

        On exit, this method:
          * flushes any residual thinking segment to event_stream
          * UPDATEs events with terminal state + finished_at +
            (for completed runs) the joined final_output_buffer
          * closes the Broadcaster
          * stops the heartbeat task
          * removes itself from app.state.active_runs (via the caller's
            finally-block or via finalize_hook — see register/unregister
            helpers below)

        Errors are caught and translated to STATE_FAILED.
        """
        # Lazy import to avoid circular dependency at module load time.
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime

        try:
            async with AgentRuntime() as runtime:
                async for event in runtime.run(
                    agent_id=agent_id,
                    user_id=user_id,
                    input_content=input_content,
                    working_source=working_source,
                    pass_mcp_urls=pass_mcp_urls or {},
                    cancellation=self.cancellation,
                    trigger_extra_data=trigger_extra_data or {},
                ):
                    # Convert non-dict messages (Pydantic / dataclasses /
                    # whatever AgentRuntime yields) into a uniform dict
                    # that emit() can route. Original fidelity preserved
                    # via _normalise_event.
                    normalised = _normalise_event(event)

                    # If run_id is not assigned yet, scan for the step-0
                    # progress message that carries details.event_id.
                    # Once found, register this BackgroundRun in
                    # active_runs and signal ready_event.
                    if self.run_id is None:
                        new_run_id = _try_extract_event_id(normalised)
                        if new_run_id:
                            await self._on_run_id_assigned(new_run_id)

                    await self.emit(normalised)
            # Natural end
            self.state = STATE_COMPLETED
            # Funnel ⑤ fires only on a genuine reply. A fatal error (e.g. no
            # provider configured) ends the generator naturally too, but the
            # user got a "configure your key" notice, not an agent reply —
            # that is not a successful round-trip.
            if not self._had_fatal_error:
                await _fire_message_success(
                    user_id=user_id, agent_id=agent_id, run_id=self.run_id,
                )
        except CancelledByUser as e:
            self.state = STATE_CANCELLED
            logger.info(f"[BackgroundRun {self.run_id}] cancelled: {e.reason}")
            # Emit stopping/cleanup + complete to live subscribers (in
            # addition to the legacy "cancelled" event for backward
            # compatibility).
            self.broadcaster.publish({"type": "stopping", "stage": "cleanup"})
            self.broadcaster.publish({"type": "stopping", "stage": "complete"})
            self.broadcaster.publish({
                "type": "cancelled",
                "message": f"Agent run stopped: {e.reason}",
            })
        except Exception as e:  # noqa: BLE001
            self.state = STATE_FAILED
            logger.exception(f"[BackgroundRun {self.run_id}] failed: {e}")
            await self._write_stream_row(
                "error",
                {"error_message": str(e), "error_type": type(e).__name__},
            )
            self.broadcaster.publish({
                "type": "error",
                "error_message": str(e),
                "error_type": type(e).__name__,
                "severity": "fatal",
            })
        finally:
            await self._finalize()

    async def _finalize(self) -> None:
        """Write terminal events row + close broadcaster + stop heartbeat
        + remove from active_runs registry.

        Idempotent — safe to call multiple times. ``state`` is the
        source of truth for which terminal value to persist.
        """
        # 1. Flush any residual thinking segment to the DB
        with suppress(Exception):
            await self._flush_segment()

        # 2. Stop the heartbeat task
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            # CancelledError is a BaseException (not Exception) since
            # Python 3.8, so suppress(Exception) does NOT catch it.
            # We explicitly list it here.
            with suppress(asyncio.CancelledError, Exception):
                await self._heartbeat_task

        # 3. Write the terminal events row (only if run_id was assigned —
        # if Step 0 failed before yielding the event_id, there is no
        # row to update because Step 0 also did the INSERT).
        if self.state not in TERMINAL_STATES:
            self.state = STATE_FAILED  # safety net — drive should always set
        if self.run_id:
            finished_at = utc_now()
            updates: dict[str, Any] = {
                "state": self.state,
                "finished_at": finished_at,
                "last_event_at": finished_at,
            }
            if self.state == STATE_CANCELLED:
                updates["error_message"] = self.cancellation.reason or "User cancelled"
            # For completed runs, populate final_output if we captured deltas.
            # AgentRuntime's step_4_persist_results also writes final_output
            # from its own bookkeeping; we don't overwrite a non-empty value.
            if self.state == STATE_COMPLETED and self.final_output_buffer:
                with suppress(Exception):
                    existing = await self.db.get_one("events", {"event_id": self.run_id})
                    if existing and not (existing.get("final_output") or "").strip():
                        updates["final_output"] = "".join(self.final_output_buffer)
            try:
                await self.db.update("events", {"event_id": self.run_id}, updates)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[BackgroundRun {self.run_id}] terminal events row update failed: {e}")

        # 4. Broadcast the terminal `complete` frame, then close the
        # broadcaster (releases all subscribers).
        #
        # This frame is the live WS path's ONLY in-band end-of-run
        # signal. The v1.0 WS handler sent {"type": "complete"} after
        # the agent loop returned; the Phase C refactor lost it — the
        # broadcaster just closed and the server closed the WS, which
        # the frontend treats as a PASSIVE disconnect. Result: every
        # normal turn end triggered the auto-reconnect machinery
        # (duplicate user bubble via run_reconnect injection, spinner
        # wiped to "Starting up…", and several non-converging branches
        # that left isStreaming stuck until a manual refresh).
        #
        # Published after the terminal events-row write (step 3) so by
        # the time the frontend reacts (refreshAgents etc.) the DB no
        # longer reports this run as active.
        self.broadcaster.publish({
            "type": "complete",
            "state": self.state,
        })
        self.broadcaster.close()

        # 5. Remove from active_runs registry
        if self.run_id and self.run_id in self._active_runs:
            self._active_runs.pop(self.run_id, None)
            logger.info(
                f"[BackgroundRun] run_id={self.run_id} terminal "
                f"state={self.state}; removed from active_runs registry "
                f"(remaining {len(self._active_runs)} entries)"
            )

        # 6. Ensure ready_event is set even on error paths so callers
        # blocked on it don't hang.
        if not self.ready_event.is_set():
            self.ready_event.set()


# ============================================================================
# Helpers — message normalisation (decoupled from the class so tests can
# drive the routing logic with synthetic dicts).
# ============================================================================

def _try_extract_event_id(event: dict) -> Optional[str]:
    """Inspect a progress message for the event_id minted by Step 0.

    Step 0 yields a ProgressMessage with step="0", status="completed",
    and details={..., "event_id": event.id, ...}. We pick it up the
    moment that message flows through drive(). Returns None for any
    other message shape.
    """
    if event.get("type") != "progress":
        return None
    details = event.get("details") or {}
    if not isinstance(details, dict):
        return None
    eid = details.get("event_id")
    return str(eid) if eid else None


def _normalise_event(event: Any) -> dict:
    """Coerce whatever AgentRuntime.run yields into a uniform dict so
    the emit() routing logic can pattern-match without isinstance
    branches everywhere."""
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
        # protocol, distinguished by step prefix or details.tool_name.
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


def _event_to_wire(event: Any) -> dict:
    """JSON-serialisable view for broadcast. Keeps the same shape the
    legacy WS code expected — to_dict / model_dump / dict passthrough."""
    return _normalise_event(event)


__all__ = ["BackgroundRun", "HEARTBEAT_INTERVAL_S",
           "STATE_RUNNING", "STATE_COMPLETED", "STATE_CANCELLED",
           "STATE_FAILED", "TERMINAL_STATES"]
