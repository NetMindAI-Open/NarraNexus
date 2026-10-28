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
* ``first_live_run_id`` — the same answer lifted to a USER, and the ONE
  cross-process source of truth for "is this user busy?". In-process
  bookkeeping cannot answer it; anything that stops containers must ask
  here (see ``executor_reaper``).
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
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from loguru import logger

from narranexus.platform.schema import (
    PHASE_RUN_AGENT_STEP,
    PHASE_RUN_AGENT_TITLE,
)
from narranexus.platform.utils.timezone import utc_now

# Liveness (heartbeat cadence, staleness threshold, ``run_is_live``) lives in
# the leaf ``utils.run_liveness`` so the circuit breaker can share the SAME
# rule without importing this module (see that module's docstring). Imported
# here for this module's own use only — callers import it from
# ``utils.run_liveness``, the one path to the rule.
from narranexus.platform.utils.run_liveness import (
    HEARTBEAT_INTERVAL_S,
    STATE_RUNNING,
    run_is_live,
)

# A plain downward import: the breaker no longer imports this module (the
# liveness rule both share lives in utils.run_liveness), so there is no cycle
# to hide behind a function-local import any more.
from narranexus.platform.agent_framework.loop.circuit_breaker import (
    bind_probe_run,
    release_orphaned_probe,
)

if TYPE_CHECKING:
    from narranexus.platform.utils.db.database import AsyncDatabaseClient


# Run state machine — string-typed for direct DB column compatibility.
STATE_COMPLETED = "completed"
STATE_CANCELLED = "cancelled"
STATE_FAILED = "failed"
TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_CANCELLED, STATE_FAILED})


# Kill switch for the trigger-path recording surface. The WS/openai
# paths (BackgroundRun) predate this switch and are NOT affected — chat
# reconnect depends on their persistence.
RECORDING_DISABLED_ENV = "NARRANEXUS_RUN_RECORDING_DISABLED"


def recording_enabled() -> bool:
    """Whether the trigger-path run recording surface is on (default yes)."""
    return os.environ.get(RECORDING_DISABLED_ENV, "").strip().lower() not in (
        "1", "true", "yes", "on",
    )


async def first_live_run_id(
    db: "AsyncDatabaseClient",
    user_id: str,
    *,
    exclude_run_id: Optional[str] = None,
) -> Optional[str]:
    """The id of a live run belonging to this user, in ANY process — or None.

    The ONE cross-process answer to "is this user busy?". In-process
    bookkeeping (``AgentAdmissionController``) cannot give it: the cloud
    orchestrator runs as backend + workers, each with its own singleton, so
    backend sees only its own WS-chat runs and workers only its own
    bus/job/channel runs. Anything that ACTS on "this user is idle" — the
    executor reaper above all — must ask here, or it acts on one process's
    partial view and cuts off a run alive in the other (2026-07-31 prod: a
    group-chat reply killed mid-flight).

    Returns the run's IDENTITY rather than a bare bool so callers can name
    what stopped them (the reaper's audit row). Which live run is returned is
    arbitrary when several exist.

    ``exclude_run_id`` exists for callers that are themselves a run: by the
    time step 3 asks, its own events row is already 'running', so counting
    itself would answer "busy" forever. First such caller is the broker's
    stale-image replacement verdict, which lands in the follow-up PR to this
    one; the reaper does not pass it (it is not a run).

    RAISES on an unreadable DB. Callers act destructively on the answer, so
    each must resolve that ambiguity itself — and every one of them resolves
    it as "busy" (see ``executor_reaper.live_run_elsewhere``).
    """
    if not user_id:
        return None
    # Projection, not SELECT *: the events row carries several MEDIUMTEXT
    # columns (env_context / module_instances / event_log) and liveness needs
    # exactly these three. started_at stays — run_is_live falls back to it
    # before the first heartbeat lands, and dropping it would make a
    # just-started run read as dead.
    rows = await db.get(
        "events",
        {"user_id": user_id, "state": STATE_RUNNING},
        fields=["event_id", "last_event_at", "started_at"],
    )
    for row in rows or []:
        event_id = row.get("event_id")
        if not event_id or (exclude_run_id and str(event_id) == exclude_run_id):
            continue
        if run_is_live(row):
            return str(event_id)
    return None


async def sweep_stale_runs(db: "AsyncDatabaseClient") -> int:
    """Settle running rows whose heartbeat died. Returns the number of rows
    flipped.

    The terminal state depends on whether a stop was pending: a row with
    ``cancel_requested_at`` set settles as 'cancelled', anything else as
    'failed'. Without that split the outcome of a user's stop would hinge on
    a race — whether the run's own finalize beat its heartbeat going stale —
    and a stop recorded as a failure feeds retry and failure alerting with
    what was actually a deliberate user action.

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
        # A stop was pending → the run died stopping, not crashing.
        stopping = bool(row.get("cancel_requested_at"))
        patch: dict = {
            "state": STATE_CANCELLED if stopping else STATE_FAILED,
            "finished_at": utc_now(),
        }
        if not stopping:
            patch["error_message"] = "run lost (process died mid-run)"
        try:
            await db.update("events", {"event_id": row["event_id"]}, patch)
            flipped += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[run-sweep] failed to mark stale run {row.get('event_id')!r}: {e}"
            )
            continue
        # A lost run never reaches its settlement (BackgroundRun._finalize or
        # the runtime client's), so its probe token died with it. If the
        # agent is PROBING and no run that started after the claim is still
        # alive, the dead run was the claimant: release it now (best-effort,
        # no verdict on the credential) instead of leaving every entry point
        # refused until the grant expires. A live run that predates the claim
        # does not keep the row PROBING (circuit_breaker._claimant_may_be_live).
        if row.get("agent_id"):
            await release_orphaned_probe(row["agent_id"], db=db)
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


def classify_event(event: dict) -> str:
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


def _extract_thinking_tier(event: dict) -> bool:
    """Is this thinking frame NexusPower narration rather than provider CoT?

    The SAME equality rule as ``chat_history_timeline.is_monologue_step`` and
    the frontend's ``isMonologueFrame``: narration only when the subset IS the
    whole frame. The batcher makes frames tier-pure, so truthiness would give
    the same answer today — but this is the copy whose verdict gets PERSISTED,
    and a wrong one here promotes provider scratchpad to the progress tier on
    every future replay of that run, where no frontend deploy can take it back.
    A guarantee enforced in another module is not a reason to skip the check.
    """
    monologue = event.get("monologue") or ""
    return bool(monologue) and monologue == (event.get("thinking_content") or "")


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
      * ``on_thinking_buffer(text, is_monologue)`` — mirror of the
        in-flight thinking segment and its tier (BackgroundRun hands it to
        the Broadcaster so mid-segment subscribers get the partial, at the
        tier the live stream will continue in).
    """

    def __init__(
        self,
        *,
        db: "AsyncDatabaseClient",
        on_run_id: Optional[Callable[[str], Awaitable[None]]] = None,
        on_thinking_buffer: Optional[Callable[[str, bool], None]] = None,
        inherited_root_run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        probe_token: Optional[str] = None,
    ) -> None:
        self.db = db
        # The circuit-breaker half-open probe claim this run carries, if any
        # (#394 review I-1). When the run's events row goes running, the
        # recorder names this run as the claimant (``bind_probe_run``), so the
        # breaker's crash-window fallback can ask about THIS run instead of
        # guessing from other runs of the agent. Both None for an ordinary run.
        self._probe_agent_id = agent_id
        self._probe_token = probe_token
        # The trigger TREE this run belongs to. Non-empty when the trigger knew
        # it was continuing somebody else's tree; otherwise this run IS a root
        # and stamps its own id at bind time. Recorded here rather than in
        # Event/create_event because it is a run-control fact (same family as
        # state / heartbeat / cancel_requested_at), not a narrative one.
        self.inherited_root_run_id = inherited_root_run_id or None
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
        self.last_action_reason: Optional[str] = None
        # Accumulated AGENT_RESPONSE deltas — the terminal write fills
        # events.final_output from this only when nothing else did.
        self.final_output_buffer: list[str] = []
        self._on_run_id = on_run_id
        self._on_thinking_buffer = on_thinking_buffer
        # Tier of what is currently buffered in _segment; a switch closes it.
        self._segment_tier: bool = False
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

        kind = classify_event(event)

        if kind == "thinking":
            tier = _extract_thinking_tier(event)
            if self._segment and tier != self._segment_tier:
                # A segment carries ONE tier, so a switch closes it — the same
                # rule the WS batcher enforces, for the same reason. Skipping
                # it here would re-create, on replay, exactly the mid-sentence
                # tear the batcher fix removed from the live stream.
                await self._flush_segment()
            self._append_to_segment(_extract_thinking_content(event), tier)
            return

        await self._flush_segment()

        if kind == "tool_call":
            self.tool_call_count += 1
            await self._write_stream_row("tool_call", _extract_tool_call_payload(event))
            # A tool call means the model is running: stamp the SAME label the
            # run-agent progress phase derives. DERIVED from the shared phase
            # constants via the same _extract_progress_stage rule the progress
            # branch uses — not a hand-copied literal — so a rename of the
            # phase title can't silently reopen the "current_stage flaps
            # between two strings for one phase" bug. Mirror into the in-memory
            # field too (same as the progress branch below), else
            # self.current_stage goes stale through a tool-heavy phase.
            self.current_stage = _extract_progress_stage(
                {"step": PHASE_RUN_AGENT_STEP, "title": PHASE_RUN_AGENT_TITLE}
            )
            await self._update_events_row({
                "tool_call_count": self.tool_call_count,
                "last_event_at": utc_now(),
                "current_stage": self.current_stage,
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
                self.last_action_reason = event.get("action_reason")
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
            # itself. This branch is now the ONLY place a fatal run lands
            # (2026-09-10, GH #127 / B-05): callers (client.py's
            # `_finalize_natural_end`, background_run.py's natural-end
            # branch) convert a naturally-ended-but-`had_fatal_error` run
            # to STATE_FAILED before calling `finalize`, instead of the
            # old "leave it STATE_COMPLETED, the cause is only in the
            # `error` stream row" behaviour -- that behaviour is exactly
            # what left ops with nothing but
            # `[AGENT-LOOP-RECOVERABLE]`/`Job failed: unknown` log lines
            # and a green "completed" row in the Run-observation UI.
            from narranexus.platform.agent_framework.llm.failure import redact_secrets
            if self.last_error_message:
                updates["error_message"] = redact_secrets(self.last_error_message)
        if self.state != STATE_CANCELLED and self.final_output_buffer:
            # A FAILED run can still have streamed a real reply before the
            # failure landed (e.g. an auth error after the text went out);
            # the row keeps that output alongside error_message rather than
            # dropping it. AgentRuntime's step_4 also writes final_output
            # from its own bookkeeping; never overwrite a non-empty value.
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
            {
                "state": STATE_RUNNING,
                "started_at": now,
                "last_event_at": now,
                # A root run labels itself; a caused run carries the label it
                # inherited. Written in the same UPDATE as the running flip so
                # a tree is complete from the first moment any of its runs is
                # visible — a cascade that raced the label would silently miss
                # the branch it was aimed at.
                "root_run_id": self.inherited_root_run_id or run_id,
            },
            context="running init",
        )
        if self._probe_token is not None and self._probe_agent_id:
            # After the running flip, so the claimant is already live by the
            # time the breaker can see its id. Never raises.
            await bind_probe_run(
                self._probe_agent_id, self._probe_token, run_id, db=self.db
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

    def _mirror_segment(self, text: str, is_monologue: bool) -> None:
        """Push the in-flight segment to the transport mirror, guarded.

        The class docstring promises an observer can never break the observed
        run; this call sits on the thinking hot path, so an observer that
        raises (a metrics tap, a test double wired to the old one-arg
        signature) would otherwise take the recording down with it.
        """
        if self._on_thinking_buffer is None:
            return
        try:  # noqa: SIM105 — the log line is the point, not a bare suppress
            self._on_thinking_buffer(text, is_monologue)
        except Exception:
            logger.exception("[RunRecorder] on_thinking_buffer observer failed")

    def _append_to_segment(self, text: str, is_monologue: bool = False) -> None:
        """Buffer one thinking frame. The caller closes the segment first on
        a tier switch, so everything buffered here shares one tier."""
        if not text:
            return
        self._segment_tier = is_monologue
        self._segment.append(text)
        self._mirror_segment("".join(self._segment), is_monologue)

    async def _flush_segment(self) -> None:
        """Persist the accumulated thinking segment as ONE row and reset
        both the local buffer and the transport mirror.

        The row KIND carries the tier: ``thinking_segment_monologue`` for
        narration, ``thinking_segment`` for provider reasoning. A new kind
        rather than a JSON payload, so rows written before this change stay
        readable exactly as they are — no backfill, no migration (iron rules
        #2 / #6). Their tier is genuinely unknown and they replay receded,
        which is what they always rendered as.
        """
        if not self._segment:
            return
        segment_text = "".join(self._segment)
        tier = self._segment_tier
        self._segment = []
        self._segment_tier = False
        self._mirror_segment("", False)
        await self._write_stream_row(
            "thinking_segment_monologue" if tier else "thinking_segment",
            segment_text,
        )

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
    "classify_event",
    "RECORDING_DISABLED_ENV",
    "RunRecorder",
    "STATE_CANCELLED",
    "STATE_COMPLETED",
    "STATE_FAILED",
    "TERMINAL_STATES",
    "event_to_wire",
    "first_live_run_id",
    "normalise_event",
    "recording_enabled",
    "sweep_stale_runs",
    "try_extract_event_id",
]
