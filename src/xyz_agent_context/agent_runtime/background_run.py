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

Composition (2026-07-31 refactor)
---------------------------------
The persistence half — event_stream rows, the events-row state machine,
heartbeat, terminal write — lives in ``RunRecorder``
(agent_runtime/run_recorder.py) so the SAME trace is produced by every
run transport (this module for WS/openai-compat; the
AgentRuntimeClient transports for every trigger). BackgroundRun is the
transport composition: RunRecorder + Broadcaster + the self-owned task
+ the active_runs registry entry.

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
Stale 'running' rows (heartbeat dead — process killed mid-run) are
settled by ``run_recorder.sweep_stale_runs`` from ``backend/main``'s
lifespan, at startup and periodically.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from xyz_agent_context.agent_runtime.admission import get_admission_controller
from xyz_agent_context.agent_runtime.broadcaster import Broadcaster
from xyz_agent_context.agent_runtime.cancellation import (
    CancellationToken,
    CancelledByUser,
)
from xyz_agent_context.agent_runtime.run_recorder import (
    HEARTBEAT_INTERVAL_S,
    RUN_STALE_AFTER_S,
    RunRecorder,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_RUNNING,
    TERMINAL_STATES,
    event_to_wire,
    normalise_event,
    parse_db_utc,
    run_is_live,
)

if TYPE_CHECKING:
    from xyz_agent_context.utils.db.database import AsyncDatabaseClient


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
      * the RunRecorder (persistence: event_stream + events lifecycle)

    BackgroundRun does NOT hand AgentRuntime construction to the
    caller — ``drive`` builds it so the run lives past the WebSocket.
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
        self.agent_id = agent_id
        self.user_id = user_id
        self.input_preview = input_preview[:200] if input_preview else ""
        self.db = db
        self._active_runs = active_runs  # the app.state.active_runs map
        self.cancellation = cancellation or CancellationToken()
        # Broadcaster is run_id-tagged but only for logging. It works
        # without run_id set.
        self.broadcaster = Broadcaster("<pending>")
        # run_id starts unset; the recorder extracts the AgentRuntime-
        # assigned event_id from the first step-0 progress message and
        # only THEN fires _on_run_id_assigned (active_runs registration +
        # ready_event). Callers that need a stable id must
        # ``await bg.ready_event.wait()`` first.
        self.recorder = RunRecorder(
            db=db,
            on_run_id=self._on_run_id_assigned,
            on_thinking_buffer=self.broadcaster.set_current_thinking_buffer,
        )
        self.state: str = STATE_RUNNING
        self._task: Optional[asyncio.Task] = None
        # Signals run_id has been assigned + active_runs registration done.
        # Callers gate "subscribe + return run_id to client" on this.
        self.ready_event: asyncio.Event = asyncio.Event()

    # ----- delegates ----------------------------------------------------

    @property
    def run_id(self) -> Optional[str]:
        return self.recorder.run_id

    @property
    def tool_call_count(self) -> int:
        return self.recorder.tool_call_count

    @property
    def current_stage(self) -> str:
        return self.recorder.current_stage

    @property
    def task(self) -> Optional[asyncio.Task]:
        return self._task

    @task.setter
    def task(self, value: asyncio.Task) -> None:
        self._task = value

    # ----- run_id late-binding ------------------------------------------

    async def _on_run_id_assigned(self, run_id: str) -> None:
        """Recorder callback — the transport half of run_id binding:
        broadcaster tag, active_runs registration, ready_event. The
        persistence half (events-row running flip + heartbeat) already
        happened inside the recorder."""
        self.broadcaster.run_id = run_id
        self._active_runs[run_id] = self
        logger.info(
            f"[BackgroundRun] run_id={run_id} bound to in-memory registry "
            f"(active_runs now has {len(self._active_runs)} entries)"
        )
        self.ready_event.set()

    # ----- public event sink (called from drive) ------------------------

    async def emit(self, event: dict) -> None:
        """Process one runtime message: persist via the recorder, then
        fan out to live subscribers. After a terminal state only the
        fan-out remains (a subscriber may still be draining)."""
        if self.state not in TERMINAL_STATES:
            await self.recorder.record(event)
        self.broadcaster.publish(event_to_wire(event))

    # ----- driver entrypoint --------------------------------------------

    async def drive(
        self,
        *,
        agent_id: str,
        user_id: str,
        input_content: str,
        working_source: Any,
        pass_mcp_servers: Optional[dict] = None,
        trigger_extra_data: Optional[dict] = None,
    ) -> None:
        """Own AgentRuntime lifecycle + consume run() to completion +
        persist everything + broadcast.

        This is the task body that ``asyncio.create_task(bg.drive(...))``
        invokes. Caller does NOT pass a runtime in — BackgroundRun manages
        AgentRuntime entirely so the run continues to live even after the
        WebSocket that started it disconnects.

        Errors are caught and translated to STATE_FAILED.
        """
        # Lazy import to avoid circular dependency at module load time.
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime

        try:
            # Two-level concurrency gate: queues the START only, never
            # interrupts a running loop (binding rule #14).
            #
            # [admission-timing]: the slot wait is the chat path's analogue
            # of the bus path's queue_wait_s — cloud-only (admission is
            # disabled locally) and entirely OUTSIDE run()'s [turn-timing]
            # total_s, so without this line "was it the queue?" is
            # unanswerable for exactly the surface the 2026-08-01 event
            # measured at 1-7min (Base recvrdLPavdQgU).
            import time as _time
            _t_admit = _time.monotonic()
            async with get_admission_controller().slot(user_id):
                _admit_wait_s = _time.monotonic() - _t_admit
                if _admit_wait_s >= 1.0:
                    # Log only real waits — an uncontended slot is noise.
                    logger.info(
                        "[admission-timing] agent={} user={} "
                        "admit_wait_s={:.2f}".format(
                            agent_id, user_id, _admit_wait_s
                        )
                    )
                async with AgentRuntime() as runtime:
                    async for event in runtime.run(
                        agent_id=agent_id,
                        user_id=user_id,
                        input_content=input_content,
                        working_source=working_source,
                        pass_mcp_servers=pass_mcp_servers or {},
                        cancellation=self.cancellation,
                        trigger_extra_data=trigger_extra_data or {},
                    ):
                        # Uniform dict for routing; original fidelity is
                        # preserved by normalise_event's passthrough.
                        await self.emit(normalise_event(event))
                # Natural end
                self.state = STATE_COMPLETED
                # Funnel ⑤ fires only on a genuine reply. A fatal error
                # (e.g. no provider configured) ends the generator naturally
                # too, but the user got a "configure your key" notice, not an
                # agent reply — that is not a successful round-trip.
                if not self.recorder.had_fatal_error:
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
            # Persist the crash into the stream (the generator died, so no
            # error event flowed through emit) and tell live subscribers.
            with suppress(Exception):
                await self.recorder.record({
                    "type": "error",
                    "error_message": str(e),
                    "error_type": type(e).__name__,
                    "severity": "fatal",
                })
            self.broadcaster.publish({
                "type": "error",
                "error_message": str(e),
                "error_type": type(e).__name__,
                "severity": "fatal",
            })
        finally:
            await self._finalize()

    async def _finalize(self) -> None:
        """Terminal bookkeeping: recorder finalize (terminal events row,
        heartbeat stop, segment flush) + circuit-breaker feed + terminal
        `complete` frame + broadcaster close + registry removal.

        Idempotent — safe to call multiple times. ``state`` is the
        source of truth for which terminal value to persist.
        """
        if self.state not in TERMINAL_STATES:
            self.state = STATE_FAILED  # safety net — drive should always set
        cancel_reason = (
            (self.cancellation.reason or "User cancelled")
            if self.state == STATE_CANCELLED
            else None
        )
        with suppress(Exception):
            await self.recorder.finalize(self.state, cancel_reason=cancel_reason)

        # Feed the Agent circuit-breaker from this turn's outcome.
        await self._record_circuit_breaker()

        # Broadcast the terminal `complete` frame, then close the
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
        # Published after the recorder's terminal events-row write so by
        # the time the frontend reacts (refreshAgents etc.) the DB no
        # longer reports this run as active.
        self.broadcaster.publish({
            "type": "complete",
            "state": self.state,
        })
        self.broadcaster.close()

        # Remove from active_runs registry
        if self.run_id and self.run_id in self._active_runs:
            self._active_runs.pop(self.run_id, None)
            logger.info(
                f"[BackgroundRun] run_id={self.run_id} terminal "
                f"state={self.state}; removed from active_runs registry "
                f"(remaining {len(self._active_runs)} entries)"
            )

        # Ensure ready_event is set even on error paths so callers
        # blocked on it don't hang.
        if not self.ready_event.is_set():
            self.ready_event.set()

    async def _record_circuit_breaker(self) -> None:
        """Advance the Agent circuit-breaker from this turn's outcome.

        Outcome mapping:
          * STATE_FAILED, or STATE_COMPLETED with a fatal error emitted
            → record_failure (a fatal auth/quota error ends the generator
              naturally, landing in STATE_COMPLETED, so state alone under-
              counts failures — the had_fatal_error flag closes that gap).
          * STATE_COMPLETED without a fatal error → record_success (resets).
          * STATE_CANCELLED → no change (user stopped it; not the agent's fault).

        Wrapped whole in try/except: the breaker is an observer and must never
        break turn finalization (incident lesson #3's corollary). Lazy import
        avoids any import-time coupling.
        """
        if not self.agent_id:
            return
        is_failure = self.state == STATE_FAILED or (
            self.state == STATE_COMPLETED and self.recorder.had_fatal_error
        )
        is_success = (
            self.state == STATE_COMPLETED and not self.recorder.had_fatal_error
        )
        if not (is_failure or is_success):
            return  # cancelled or otherwise — leave breaker state untouched
        try:
            from xyz_agent_context.agent_framework.loop import circuit_breaker as cb
            if is_failure:
                await cb.record_failure(
                    self.agent_id,
                    self.recorder.last_error_type,
                    self.recorder.last_error_message,
                )
            else:
                await cb.record_success(self.agent_id)
        except Exception as e:  # noqa: BLE001 — observer never breaks observed
            logger.warning(
                f"[BackgroundRun {self.run_id}] circuit-breaker record failed: {e}"
            )


__all__ = ["BackgroundRun", "HEARTBEAT_INTERVAL_S", "RUN_STALE_AFTER_S",
           "STATE_RUNNING", "STATE_COMPLETED", "STATE_CANCELLED",
           "STATE_FAILED", "TERMINAL_STATES",
           "parse_db_utc", "run_is_live"]
