"""
@file_name: message_bus_trigger.py
@author: NarraNexus
@date: 2026-04-03
@description: Background poller that delivers pending messages to agents

Polls bus_messages table, triggers AgentRuntime for agents with
unprocessed messages.

Design:
- Single poller cycles through all registered agents (from bus_agent_registry)
- Groups pending messages by channel_id (per-channel batching)
- For each channel with pending messages, triggers AgentRuntime.run()
- On success: advances the cursor via ack_processed()
- On failure: records failure via record_failure()

Usage:
    DATABASE_URL=sqlite:///path/to/db uv run python -m narranexus.platform.message_bus.message_bus_trigger
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from narranexus.platform.agent_framework.llm.failure import (
    MAX_REDACTED_ERROR_LEN,
    is_credential_error,
    redact_secrets,
)
from narranexus.platform.services.service_audit import ServiceAuditor
from narranexus.platform.message_bus._bus_activity import (
    elapsed_seconds,
    is_live,
    is_stalled,
)
from narranexus.platform.message_bus.local_bus import (
    PENDING_BATCH_LIMIT,
    PENDING_BATCH_LIMIT_WIDE,
    POISON_FAILURE_THRESHOLD as _POISON_FAILURE_THRESHOLD,
    LocalMessageBus,
    _as_utc,
    canonical_ts,
)
from narranexus.platform.message_bus.delivery_notice import (
    announce_processing_failure,
    UNDELIVERED_MSG_TYPE,
    announce_delivery_failure,
    announce_undelivered,
)
from narranexus.platform.message_bus.multipart import assemble as assemble_parts
from narranexus.platform.utils.inline_field import (
    INLINE_DESCRIPTION_MAX_CHARS,
    body_lines,
    inline_field,
    quoted_block,
)
from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    SYSTEM_SENDER_LABEL,
    placeholders as _platform_placeholders,
    trigger_label as _platform_trigger_label,
)
from narranexus.platform.message_bus.schemas import BusMessage
from narranexus.platform.repository.bus_delivery_receipt_repository import (
    RECEIPT_DROPPED,
    RECEIPT_FAILED,
    RECEIPT_PROCESSED,
    RECEIPT_RELAYED,
    RECEIPT_SILENT,
)
from narranexus.platform.channel.message_source_handler import (
    im_channel_prefixes,
)
from narranexus.platform.message_bus.team_posting import (
    MAX_TEAM_AGENT_HOPS,
    extract_team_mentions,
    mention_token,
)
from narranexus.platform.schema import (
    BUS_PLAIN_TEXT_TURN_EXTRA_KEY,
    BUS_TEAM_ROOM_EXTRA_KEY,
    WorkingSource,
)
from narranexus.platform.schema.turn_profile import TurnProfile
from narranexus.platform.settings import settings
from narranexus.platform.utils.timezone import utc_now

# Poll interval in seconds (initial; adaptive bounds below)
POLL_INTERVAL = 3

# Maximum concurrent agent processing workers. Read from settings so a
# deployment can raise it without a code change — see `bus_max_workers` there
# for why the old hard-coded 3 was a problem. Resolved at import time on
# purpose: the trigger is a long-lived process and a mid-flight change to the
# semaphore's capacity has no meaning.
MAX_WORKERS = settings.bus_max_workers

# How long the pool must stay saturated-with-a-queue before it is called
# starvation. One busy moment is a pool doing its job; a sustained stretch is
# the pool being the bottleneck.
#
# WALL CLOCK, not a cycle count — and that distinction is load-bearing. Poll
# cycles get RARER exactly during starvation: `_poll_cycle` returns 0 dispatches
# while candidates queue behind the semaphore, so the adaptive interval backs
# off 3 -> 6 -> 9 -> 12s. A cycle-counting threshold therefore samples least
# often when it most needs to. Measured on a live instance (2026-08-14,
# bus_max_workers=1, two agents): a real 28-second starvation produced only FOUR
# cycles, and a five-cycle threshold missed it entirely.
STARVATION_ALERT_AFTER_S = 20.0

# Rate limiting constants
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 1800  # 30 minutes in seconds

# Adaptive polling constants. Kept low so a team group-chat reply lands quickly
# (the trigger is a separate process; this is the latency the user feels after
# an idle period). Worst-case idle latency ≈ POLL_MAX_INTERVAL.
POLL_MIN_INTERVAL = 3
POLL_MAX_INTERVAL = 12
POLL_STEP_UP = 3

#: steer_inbox retention, run by the poll loop (startup + daily). This trigger is
#: steer_inbox's only production writer, so it owns its retention — without this
#: the table would be write-only. Two bounds passed to
#: SteerInboxRepository.cleanup_older_than_days:
#: * CONSUMED rows kept this long (they are done — brief keep for audit);
#: * UNCONSUMED rows past ORPHAN days are dead-run orphans (the teardown discard's
#:   backstop). ORPHAN is deliberately generous (>> any turn, incl. binding rule
#:   #14's tens-of-hours jobs whose rows drain as they run) so a live,
#:   actively-draining run is never touched.
STEER_RETENTION_DAYS = 3
STEER_ORPHAN_DAYS = 7
STEER_CLEANUP_INTERVAL_S = 24 * 3600

#: Retention for the two 2026-09-09 ledgers this trigger is the production
#: writer of, swept on the same daily tick. Both bounds sit far above every
#: window that reads the rows: the receipt guards and the owner-notice
#: cooldowns are FAILURE_NOTIFY_COOLDOWN_SECONDS (30 min) wide, so a sweep can
#: never reach into a live window — which would re-open it and bring back the
#: duplicate notice B-20.3 removed.
RECEIPT_RETENTION_DAYS = 30
NOTICE_COOLDOWN_RETENTION_DAYS = 2

# How often the poll loop checks the cross-process wake signal while it sleeps.
# Bounds the added latency of a send made outside this process; 0.5s keeps that
# under a second while costing two single-row reads per second.
WAKE_SIGNAL_SLICE = 0.5

# Team group chat: cap how many consecutive agent-to-agent hops can keep the
# @-mention cascade alive without a human message. Past this, an agent reply's
# @mentions are dropped so two agents can't @ each other forever. A user
# message resets the chain.

# Team group chat: how many recent room messages to feed a triggered agent as
# context (oldest→newest). The agent replies to the latest message addressed to
# it, but SEES the recent scrollback — incl. a shared image/file posted by
# someone else — so it can Read and discuss it without a manual relay. Capped to
# bound the per-turn token cost.
TEAM_HISTORY_LIMIT = 20

# How much of a team's `intro_md` rides the prompt. The column is MEDIUMTEXT and
# the field is a free-text box in the management UI, so an owner can paste a
# manual into it — unbounded here would crowd out the scrollback and the roster,
# which are the parts that decide what the agent does THIS turn. Truncation is
# always announced; silently cutting an owner's house rules would be worse than
# not carrying them.

TEAM_INTRO_MAX_CHARS = 1200

#: How many board rows ride the prompt. The board is injected into every
#: member's context on every turn, and since `message_bus/errand.py` began
#: opening errands from @mentions its length follows room traffic rather than
#: a Leader's deliberate tool calls. Overflow is announced, never silently
#: dropped — see the render site.
TEAM_BOARD_MAX_ITEMS = 15

#: Outcomes of the in-turn room deliverer, as three states rather than two.
#: "the runtime never called it" is NOT "it landed" — reading it that way books
#: an undelivered turn as a completed hop and announces a failure to a room
#: that may already have heard the agent.

# Owned by local_bus (whose `get_pending_messages` enforces the filter) and
# imported here so the two can't drift. Once a message's failure_count reaches
# it, the message is permanently dropped from the pending queue with no further
# retries — see `_notify_permanent_failure` below, which is the only signal the
# owner gets when that happens.
POISON_FAILURE_THRESHOLD = _POISON_FAILURE_THRESHOLD

# De-dup window for owner-facing inbox notices, keyed per
# (agent_id, channel_id, category) and PERSISTED (`owner_notice_cooldowns`,
# see `_notify_owner`). Same window as the rate limiter — a batch of messages
# failing for one root cause (e.g. a broken provider key) should not write one
# inbox row per message. Per channel, not per agent: a hand-off that broke on
# channel A must not silence the notice about an unrelated failure on B.
FAILURE_NOTIFY_COOLDOWN_SECONDS = 1800  # 30 minutes

# Credential-error classification and secret redaction moved to the shared
# ``agent_framework.llm.failure`` module so every background LLM path (bus,
# narrative updater, Step-5 hooks) asks the same questions the same way.
# ``_classify_error`` / ``_redact_error_for_owner`` below delegate to it.
MAX_NOTIFIED_ERROR_LEN = MAX_REDACTED_ERROR_LEN


@dataclass(frozen=True)
class TurnResult:
    """What one bus turn produced, and whether it reached anyone.

    ``delivered`` is the question a bare ``text`` could never answer. On a bus
    turn the collected text is only ONE of two delivery surfaces: a peer is
    reached exclusively by a bus send TOOL, and that tool's output never
    appears in ``text``. So an empty ``text`` alone does not mean "nothing was
    delivered" — asserting it would print "no reply" underneath a reply that
    landed perfectly well.

    Answered by asking the MessageSource registry (the one place that already
    knows which tools deliver on a bus turn) rather than by matching tool names
    here — a second list would drift from it the day a third send tool appears,
    which is exactly how the 2026-08-01 no-reply metric got poisoned.
    """

    text: str
    event_id: Optional[str]
    delivered: bool = False
    #: The turn produced no usable output at all — a distinct question from
    #: ``delivered`` ("did anything reach a recipient"). A fatal turn's ``text``
    #: is a failure notice, not the agent's words, so a consumer that posts
    #: plain text needs to know not to.
    fatal: bool = False

    @property
    def reached_nobody(self) -> bool:
        """No text for the owner AND no tool that reached anyone."""
        return not self.text and not self.delivered


def _log_wake_task_failure(task: "asyncio.Task") -> None:
    """Surface a cross-process wake task that died, and swallow the cancel.

    Cancellation is the normal path — `_sleep_until_due` cancels the loser of
    every cycle — so only a real exception is worth a line.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            f"[bus-wake] cross-process waiter died: {type(exc).__name__}: {exc}"
        )


def build_bus_anchor(messages: List[BusMessage]) -> str:
    """Build the clean retrieval anchor for a bus turn.

    The execution prompt (_build_prompt) wraps peer messages in a per-turn
    ~1217-char Owner-Relay boilerplate + From/Time metadata — bus was the only
    real 400 source in prod. The anchor keeps ONLY each peer's body (tagged
    with the sender agent), so the narrative query vector is clean. Oversized
    backlogs are still capped downstream by a length guard.
    See the 2026-06-01 design doc.
    """
    return "\n".join(
        f"[From agent {m.from_agent}] {m.content}" for m in messages
    )


@dataclass
class _InFlight:
    """One dispatched agent turn the poll loop is deliberately not awaiting."""

    task: asyncio.Task
    started_at: float
    # Flipped once the turn actually holds a worker slot. A dispatch that is
    # still False is queued behind `_semaphore`, and the gap between the two
    # counts is exactly the slot-starvation signal the heartbeat reports.
    running: bool = False
    # The newest message this turn's prompt already carries (its trigger
    # batch's high-water = the timestamp it will ack to at turn END). Steering
    # into a live run must inject ONLY messages newer than this, or it would
    # re-deliver the very batch the turn is already acting on (the trigger
    # cursor does not advance until the turn ends). None until the turn declares
    # it — which happens before the run becomes steerable, so `_route_steer`
    # (gated on a registered SteerChannel) always sees it set.
    rendered_through: Optional[str] = None
    # The newest CONSUMED steered message's canonical created_at, raised by the
    # consumption callback. The turn-end read-cursor ack extends its upper bound
    # to this so a steered-and-consumed message is marked read — but only through
    # the single gap-guarded writer (`_ack_room_seen`), never from the
    # consumption callback directly (that would bypass the gap protection).
    steered_through: Optional[str] = None
    # Set when a NEW message arrived this turn that was NOT steered (un-addressed
    # to this agent, or arrived after the prompt was built). It sits between the
    # rendered window and the steered high-water and was never shown, so the
    # read cursor must NOT jump over it — `_ack_room_seen` falls back to the
    # trigger high-water when this is set.
    unsteered_gap: bool = False
    # How many `_route_steer` cycles are executing for this lane right now.
    # `_ack_room_seen` treats >0 like `unsteered_gap`: a cycle still in flight may
    # be about to discover an un-rendered message after its awaits, so the read
    # cursor cannot safely extrapolate to steered_through until it settles. The
    # default-conservative half of the read-cursor gate (the flag is the other
    # half); +1 at _route_steer entry, -1 in its finally.
    steer_cycles_in_flight: int = 0


class MessageBusTrigger:
    """
    Background poller that processes pending MessageBus messages.

    Finds LANES (agent, channel) with unprocessed messages and triggers
    AgentRuntime per lane, so one agent runs its several rooms concurrently.

    Args:
        bus: A MessageBusService instance (typically LocalMessageBus).
        poll_interval: Seconds between poll cycles.
        max_workers: Maximum concurrent agent processing tasks.
    """

    def __init__(
        self,
        bus: LocalMessageBus,
        poll_interval: int = POLL_INTERVAL,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self._bus = bus
        self._poll_interval = poll_interval
        self._max_workers = max_workers
        self._running = False
        self._semaphore = asyncio.Semaphore(max_workers)
        self._rate_counters: Dict[str, List[float]] = {}
        self._current_interval = poll_interval
        # Per-LANE serialisation lock, lane = (agent_id, channel_id). The global
        # ``_semaphore`` caps concurrent turns but does NOT prevent the same
        # pending message being dispatched twice in parallel —
        # `get_pending_messages` only filters on ``last_processed_at``, which is
        # advanced after ``_invoke_runtime`` returns. AgentRuntime takes minutes
        # for an LLM-heavy turn; the poll loop fires every ~10s; without this
        # lock the same bus_message gets handed to AgentRuntime 3+ times.
        # Observed in production (2026-05-12 13:20 — msg_4eb528dc processed three
        # times, burned ~30K tokens). The duplicate risk is per-message hence
        # per-channel, so keying by lane keeps the guarantee while letting one
        # agent run its several teams concurrently — a message in team B must not
        # wait behind the agent's team-A turn. Unbounded (agent×channel keys, no
        # cleanup): bounded in practice by the roster, like `_in_flight`.
        self._lane_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        # In-flight dispatches, LANE (agent_id, channel_id) -> _InFlight. The
        # poll loop spawns these and does NOT await them (see `_poll_cycle`), so
        # this is both the "don't dispatch the same LANE twice" guard and the
        # raw material for the audit heartbeat. One agent may hold several lanes
        # at once (its concurrent teams).
        self._in_flight: Dict[Tuple[str, str], _InFlight] = {}
        # Wakes the poll loop out of its interval sleep on stop().
        self._stop_event = asyncio.Event()
        # Wakes the poll loop because WORK just landed, not because we are
        # shutting down. Set by a successful team-room post; see `_wake`.
        self._wake_event = asyncio.Event()
        #: Cross-process wake signal as of the TOP of the current poll cycle.
        #: None means "never read" — which reads as a difference on the first
        #: slice and costs one early scan, the safe direction.
        self._wake_baseline: Optional[str] = None
        # L2/L3 observability. This trigger was the only long-running worker
        # without its own auditor: the supervisor's aggregate liveness only
        # proves the asyncio task object still exists, so when the poll loop
        # wedged on 2026-07-27 it reported "running" for 33 hours while zero
        # messages moved. The counters below are what make a wedge visible —
        # a frozen `cycles` means the loop is stuck, a frozen
        # `dispatched_total` alongside a non-zero `candidates` means messages
        # are piling up unserved.
        self.audit = ServiceAuditor("message_bus_trigger")
        # When the pool first went saturated-with-a-queue (monotonic), and
        # whether this episode already produced its one alert. None = not
        # currently starved. See `_check_worker_starvation`.
        self._starvation_since: Optional[float] = None
        self._starvation_alerted = False
        self._cycles = 0
        self._dispatched_total = 0
        self._handled_total = 0
        self._last_candidates = 0
        self._last_dispatch_at: Optional[str] = None
        # steer_inbox retention gate. -inf so the first poll cycle runs it once at
        # startup, then STEER_CLEANUP_INTERVAL_S apart (like ChannelTriggerBase).
        self._last_steer_cleanup_monotonic = float("-inf")

    async def start(self) -> None:
        """Start the polling loop with adaptive interval."""
        self._running = True
        self._stop_event.clear()
        logger.info(
            f"MessageBusTrigger started (poll_interval={self._poll_interval}s, "
            f"max_workers={self._max_workers})"
        )
        await self.audit.started({
            "poll_interval": self._poll_interval,
            "max_workers": self._max_workers,
        })

        while self._running:
            try:
                # BEFORE the scan, not at sleep entry. A `message_team` posted
                # from the MCP server while `_poll_cycle` is running bumps the
                # signal; if the sleeper read its own baseline afterwards it
                # would fold that bump in and then wait for a FURTHER change,
                # so the message sat out the whole adaptive interval — the dead
                # air the cross-process wake exists to remove. The in-process
                # `_wake_event` has no such hole: it is `.set()` during the scan
                # and only cleared at the end of the sleep.
                await self._snapshot_wake_baseline()
                dispatched = await self._poll_cycle()
                if dispatched:
                    self._current_interval = POLL_MIN_INTERVAL
                else:
                    self._current_interval = min(
                        self._current_interval + POLL_STEP_UP,
                        POLL_MAX_INTERVAL,
                    )
            except Exception as e:
                logger.exception(f"MessageBusTrigger poll cycle error: {e}")
                await self.audit.error({"stage": "poll_cycle", "error": repr(e)})

            # Throttled inside ServiceAuditor (60s), so this is cheap per cycle.
            await self.audit.heartbeat(self.liveness_snapshot())
            # The heartbeat has always CARRIED the starvation signal; this is
            # what finally reads it.
            await self._check_worker_starvation()
            # steer_inbox retention (startup + daily). Gated so it is one query a
            # day, not per cycle.
            await self._maybe_run_steer_cleanup()
            await self._sleep_until_due()

        await self.audit.stopped(self.liveness_snapshot())

    async def _post_to_room(
        self,
        *,
        from_agent: str,
        to_channel: str,
        content: str,
        mentions: Optional[List[str]] = None,
        msg_type: str = "text",
        event_id: Optional[str] = None,
        segments: Optional[List[dict]] = None,
    ) -> str:
        """Put a message in a room, tell the poll loop to look again, and
        return the new message id.

        The id is returned because the errand layer keys its dedup on the
        MESSAGE (see `repository.team_work_repository.has_errand_for`), and the
        only alternative — re-reading the room to find the row just written —
        would race the very poll loop this method just woke.

        The only way THIS PROCESS's own posts should reach the bus — the trigger's
        two of them. It is no longer the only post path in the system: a team
        reply is a `message_team` tool call that goes through
        `team_posting.post_team_reply` -> `bus.send_message` from the MCP server,
        and that is correct rather than an oversight. What replaced this method's
        guarantee for that path is `wake_signal`, bumped inside `send_message`
        itself, i.e. at the write seam where it cannot be skipped.

        Still worth keeping for the two local callers, and not because posting
        needs abstracting — the call is one line — but because "post" and "wake"
        must not be separable. They were, and the second of two call sites was
        already missing the wake: the leader patrol posts under the room's own
        marker and @-mentions members, so a patrolled teammate became pending
        and then waited out a full adaptive interval to be noticed. Platform-
        initiated dead air, which reads worse than an agent being slow.

        A third caller cannot repeat that mistake without going out of its way.

        The wake fires only after `send_message` RETURNS: a post that threw put
        nothing in the room, so there is nothing new to look at
        (`test_a_failed_room_post_does_not_wake`). Exceptions propagate — the
        team-reply site has its own handler for the "reply exists, room will
        never show it" case, and the patrol site is content to let a failure
        surface.

        Two callers: the team-room failure notice and the leader patrol line. The
        team reply was a third while the trigger posted it (`_deliver_reply`);
        that method is gone and the reply arrives via the tool now.

        The notice's wake finds nothing to dispatch — a platform line mentions
        nobody — so it costs one indexed query and is left in rather than
        special-cased, because "post and wake are inseparable" is worth more
        than one avoided empty cycle.
        """
        # Parameters listed explicitly rather than **kwargs: a passthrough
        # signature hides a misspelled kwarg from pyright and only surfaces it
        # as a runtime TypeError — on the patrol path, an unhandled one.
        #
        # Which is exactly what an explicit list costs if it falls behind: this
        # funnel and the segments-carrying reply landed in parallel branches, and
        # for one merge every team reply raised TypeError here, got caught by the
        # caller's "the room will never show this" handler, and was announced as
        # a delivery failure instead of being posted. Anything `send_message`
        # accepts and a room caller passes has to appear here too.
        message_id = await self._bus.send_message(
            from_agent=from_agent,
            to_channel=to_channel,
            content=content,
            mentions=mentions,
            msg_type=msg_type,
            event_id=event_id,
            segments=segments,
        )
        # Unconditional rather than "only when mentions is non-empty": an
        # owner-addressed reply can also make the room's lead due, and one extra
        # poll cycle costs a single indexed query.
        self._wake()
        return message_id or ""

    def _wake(self) -> None:
        """Ask the poll loop to look again now instead of at the next tick.

        Called when THIS process just put work into the bus — every in-process
        post goes through `_post_to_room`: a team-room reply (from inside the
        turn), a leader patrol line, or a team-room failure notice. The relay gap acceptance #5 is about is not inside a
        turn, it is between turns: A finishes and posts, B is mentioned in that
        post, and B then waits out a full poll interval (3-12s) to be noticed.
        Stacked across a three-hop relay that is most of the dead air a person
        in the room sees.

        Cheap and idempotent: an Event that is already set stays set, and the
        sleep clears it on the way out.

        **Only covers posts made by this process** — which is now the smaller
        half. An agent's reply is a tool call from the MCP server, where an
        in-process Event cannot reach. That gap is covered by `wake_signal`: a
        row bumped inside `send_message` and polled by
        `_wait_cross_process_wake`, 40 lines below. This Event remains the
        cheaper shortcut for the trigger's own two posts, not the only mechanism.

        (An earlier version of this paragraph said a cross-process signal was
        "worth doing if peer-DM latency ever becomes the complaint, not needed
        for team relay". It became needed the moment the team reply itself moved
        to the MCP server, and it exists — the sentence outlived the design it
        described by one commit.)
        """
        self._wake_event.set()

    async def _sleep_until_due(self) -> None:
        """Sleep the adaptive interval, cut short by either stop or new work.

        Was a bare `wait_for(self._stop_event.wait(), ...)`. Stop still ends it
        — a SIGTERM must not wait out POLL_MAX_INTERVAL — but a room post now
        ends it too, so the room's own delivery schedules the next hop instead
        of a timer noticing later.
        """
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        cross_task = asyncio.create_task(self._wait_cross_process_wake())
        # Every create_task pairs with a done callback (incident lesson #2). This
        # one is internally guarded, so the callback should never fire — which is
        # exactly why it is here: if it ever does, the `.cancel()` in `finally`
        # is a no-op on an already-failed task and asyncio would otherwise log
        # "Task exception was never retrieved" once per poll cycle, i.e. a real
        # fault reported as recurring noise nobody reads.
        cross_task.add_done_callback(_log_wake_task_failure)
        try:
            await asyncio.wait(
                {stop_task, wake_task, cross_task},
                timeout=self._current_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            cross_task.cancel()
            # Both are cancelled every cycle, including the winner (already
            # done, so the cancel is a no-op). Leaving either pending would
            # leak one waiter per poll cycle onto the Events.
            stop_task.cancel()
            wake_task.cancel()
            # Cleared here rather than at the call site: whoever woke us has
            # had its effect, and a flag left set would make the NEXT sleep
            # return instantly and spin the loop.
            self._wake_event.clear()

    async def _snapshot_wake_baseline(self) -> None:
        """Remember the signal's value as of the top of this poll cycle.

        Split from the sleeper so the baseline predates the scan.

        Fails open, but not by keeping the old value: `wake_signal.read` swallows
        its own errors and returns `""`, so an unreadable signal CLOBBERS the
        baseline to the empty string. The `except` here is reachable only via
        `get_db_client()`. The direction is still the safe one — an empty baseline
        differs from any real value, so the next sleep returns early and costs one
        wasted scan rather than leaving a message to wait out the interval — but
        the mechanism is a clobber, not a retention, and a reader reasoning from
        the old wording would expect the opposite failure.
        """
        from narranexus.platform.message_bus import wake_signal
        from narranexus.platform.utils.db.db_factory import get_db_client

        try:
            self._wake_baseline = await wake_signal.read(await get_db_client())
        except Exception:  # noqa: BLE001 — see docstring
            pass

    async def _wait_cross_process_wake(self) -> None:
        """Return as soon as ANOTHER process reports new work.

        The in-process `_wake_event` cannot be set from the MCP server, and a
        team reply is now a tool call made there — so without this the room's own
        relay would wait out the adaptive interval (3-12s per hop), handing back
        part of the latency win `c7739ad1` measured, as dead air a person in the
        room can see (iron rule #16).

        Polls a single-row signal rather than the pending-work scan: the scan is
        the expensive query this exists to schedule, so running it every slice
        would defeat the point. One tiny read per slice, two reads a second.

        Fails OPEN and quietly: an unreadable signal means "no news" and the
        caller's timeout takes over. Raising would take the poll loop down over
        a latency optimisation. The observable that catches a signal which
        stopped working is `queue_wait` in the `[bus-timing]` line, not this
        function's silence (iron-rule lesson #4).
        """
        from narranexus.platform.message_bus import wake_signal
        from narranexus.platform.utils.db.db_factory import get_db_client

        try:
            db = await get_db_client()
        except Exception:  # noqa: BLE001 — no signal, no early wake
            await asyncio.sleep(self._current_interval)
            return

        # Taken at the top of the cycle by `_snapshot_wake_baseline`, so a bump
        # that landed DURING the scan is already a difference when the first
        # slice reads.
        baseline = self._wake_baseline

        while True:
            await asyncio.sleep(WAKE_SIGNAL_SLICE)
            try:
                if await wake_signal.read(db) != baseline:
                    return
            except Exception:  # noqa: BLE001
                return

    async def _check_worker_starvation(self) -> None:
        """Alert when the WORKER POOL, not the agents, is the bottleneck.

        `liveness_snapshot()` has reported `running` / `waiting` / `max_workers`
        since the 2026-07-27 wedge, and its docstring already names the pattern:
        sustained `running == max_workers` with `waiting > 0` means turns are
        queued behind slots rather than behind their own work. Until now nobody
        read it — the numbers went into a heartbeat row and stopped there.

        This matters for latency specifically: slot wait sits INSIDE
        the `queue_wait_s` the `[bus-timing]` line reports, which is what PRD
        acceptance #1 is judged on. Without this signal a starved pool is
        indistinguishable from
        "everyone's turns got slower".

        Three deliberate properties:

        * **A duration, not an instant.** One saturated moment is a pool being
          used; `STARVATION_ALERT_AFTER_S` of it is a shortage. Measured in
          wall clock rather than cycles because cycles get rarer during
          starvation — see the constant.
        * **Once per episode.** A pool saturated for an hour is one problem, not
          sixty rows. An alarm that fires every cycle becomes noise nobody
          reads (lesson #3).
        * **Diagnostic only.** Nothing here cancels, force-stops or reprioritises
          anything — a multi-hour turn is a legitimate workload (binding rule
          #14). And it does NOT reach the owner's inbox: a slot shortage is a
          platform problem the owner cannot act on, and putting it there would
          only train them to ignore the inbox.
        """
        snap = self.liveness_snapshot()
        starved = (
            snap["running"] >= snap["max_workers"] and snap["waiting"] > 0
        )
        now = time.monotonic()
        if not starved:
            self._starvation_since = None
            self._starvation_alerted = False
            return

        if self._starvation_since is None:
            self._starvation_since = now
        starved_for = now - self._starvation_since
        if starved_for < STARVATION_ALERT_AFTER_S or self._starvation_alerted:
            return

        self._starvation_alerted = True
        logger.warning(
            f"[bus] worker pool saturated for {starved_for:.0f}s: "
            f"running={snap['running']}/{snap['max_workers']} "
            f"waiting={snap['waiting']} distinct_agents={snap['distinct_agents']} "
            f"longest={snap['longest_running_lane']} ({snap['longest_running_s']}s). "
            f"Raise settings.bus_max_workers if this persists."
        )
        await self.audit.error({
            "stage": "worker_starvation",
            "starved_for_s": int(starved_for),
            "running": snap["running"],
            "waiting": snap["waiting"],
            "max_workers": snap["max_workers"],
            # Names WHICH lane to go look at (agent@channel) and whether one
            # agent is hogging the pool (distinct_agents == 1) or many each hold
            # one. Diagnostic only — nothing acts on it.
            "distinct_agents": snap["distinct_agents"],
            "longest_running_lane": snap["longest_running_lane"],
            "longest_running_s": snap["longest_running_s"],
        })

    def stop(self) -> None:
        """Signal the polling loop to stop and drop any in-flight dispatches.

        Cancelling here is shutdown, not a policy on run length: the process is
        going away either way, and leaving the tasks would just leak them past
        the loop that owns them.
        """
        self._running = False
        self._stop_event.set()
        for lane, flight in list(self._in_flight.items()):
            flight.task.cancel()
            logger.info(f"MessageBusTrigger: cancelling in-flight turn for {lane}")
        logger.info("MessageBusTrigger stopping")

    def liveness_snapshot(self) -> Dict[str, Any]:
        """Work counters for the audit heartbeat (L2 + L3).

        Read this, not "is the process up", to tell a wedged bus from an idle
        one: `cycles` advances whenever the loop is alive, `dispatched_total`
        advances whenever work actually starts, and `candidates` says whether
        there was anything to do. Frozen `cycles` = the loop is stuck. Advancing
        `cycles` with a frozen `dispatched_total` and non-zero `candidates` =
        the loop is fine but nothing can start.

        `running` vs `waiting` is the slot-starvation signal: sustained
        `running == max_workers` with `waiting > 0` means the worker pool, not
        the agents, is the bottleneck.

        `longest_running_s` / `longest_running_lane` (agent@channel) are
        DIAGNOSTIC ONLY — they name WHICH lane is holding a slot so a human can
        look, and `distinct_agents` says whether one agent is hogging the pool
        (== 1) or many each hold one. `longest_running_agent` is kept for older
        readers. Nothing here ever force-stops a turn; a multi-hour run is a
        legitimate workload
        (binding rule #14), and the failure mode this guards against is our own
        loop dying, not an agent taking its time.
        """
        now = time.monotonic()
        running = sum(1 for f in self._in_flight.values() if f.running)
        # Now that the unit is the lane, an agent can hold several slots at once
        # (one per busy room). Report the full lane of the longest holder — the
        # channel is exactly what on-call needs to know WHICH room is wedged, and
        # dropping it (folding back to the agent) lowers the diagnostic below what
        # the 2026-07-27 33h stall was located with (incident lesson #4). Also
        # report distinct_agents so starvation can be read as "one agent hogging
        # the pool" vs "many agents each holding one".
        longest_agent, longest_channel, longest_s = None, None, 0
        running_agents: set = set()
        for (lane_agent, lane_channel), flight in self._in_flight.items():
            if not flight.running:
                continue
            running_agents.add(lane_agent)
            elapsed = int(now - flight.started_at)
            # `is None` first: a turn that started this second has elapsed 0 and
            # must still be named, or a freshly-wedged slot reports as nobody.
            if longest_agent is None or elapsed > longest_s:
                longest_agent, longest_channel, longest_s = (
                    lane_agent, lane_channel, elapsed,
                )
        longest_lane = (
            f"{longest_agent}@{longest_channel}" if longest_agent is not None else None
        )
        return {
            "cycles": self._cycles,
            "candidates": self._last_candidates,
            "dispatched_total": self._dispatched_total,
            "handled_total": self._handled_total,
            "running": running,
            "waiting": len(self._in_flight) - running,
            "max_workers": self._max_workers,
            "distinct_agents": len(running_agents),
            "longest_running_s": longest_s,
            "longest_running_agent": longest_agent,
            "longest_running_lane": longest_lane,
            "last_dispatch_at": self._last_dispatch_at,
        }

    async def _lanes_with_pending(self) -> List[Tuple[str, str]]:
        """Lanes ``(agent_id, channel_id)`` that have a message past the cursor.

        One query replacing "every agent that is a member of any channel" —
        364 of them on prod, each of which then ran its own
        ``get_pending_messages`` (plus a poison lookup per row) every few
        seconds just to conclude it had nothing to do.

        Returns LANES, not agents: the poll loop dispatches and gates per
        ``(agent, channel)`` so one agent's teams run concurrently. Deliberately
        a CANDIDATE set: it mirrors ``get_pending_messages``' cursor +
        not-self-sent predicate but skips the poison and @mention filters, which
        stay in ``_process_lane`` where the real decision is made. Over-including
        is free; under-including would drop a message.
        """
        rows = await self._bus._db.execute(
            "SELECT DISTINCT cm.agent_id AS agent_id, cm.channel_id AS channel_id "
            "FROM bus_channel_members cm "
            "JOIN bus_messages m ON m.channel_id = cm.channel_id "
            "WHERE m.created_at > COALESCE(cm.last_processed_at, '1970-01-01') "
            "AND m.from_agent != cm.agent_id",
            (),
        )
        return [(r["agent_id"], r["channel_id"]) for r in rows] if rows else []

    def _dispatch(self, agent_id: str, channel_id: str) -> None:
        """Spawn a supervised turn for one lane and return immediately."""
        lane = (agent_id, channel_id)
        task = asyncio.create_task(self._run_dispatch(agent_id, channel_id))
        self._in_flight[lane] = _InFlight(task=task, started_at=time.monotonic())
        # Paired done-callback: an unawaited task's exception would otherwise
        # surface only as a GC warning (incident lesson #2).
        task.add_done_callback(lambda t, ln=lane: self._on_dispatch_done(ln, t))

    async def _run_dispatch(self, agent_id: str, channel_id: str) -> None:
        handled = await self._process_lane(agent_id, channel_id)
        if handled:
            self._handled_total += 1

    def _on_dispatch_done(self, lane: Tuple[str, str], task: asyncio.Task) -> None:
        self._in_flight.pop(lane, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                f"MessageBusTrigger: dispatch for {lane} died: {exc!r}",
                exc_info=exc,
            )

    async def _poll_cycle(self) -> int:
        """Run one poll cycle. Returns how many turns it dispatched.

        The cycle **does not await the work it starts**. It used to
        ``asyncio.gather`` every agent and wait for all of them, which meant a
        single coroutine that never returned — an LLM connection wedged with no
        timeout, holding one of the ``max_workers`` slots — froze the entire
        loop. That is exactly what happened on prod 2026-07-27: 33 hours with
        zero messages processed for anyone, no exception, no restart, while
        liveness still read "running".

        Now a stuck turn holds its own task and its own slot; the loop keeps
        cycling, the heartbeat keeps reporting, and `_in_flight` names the
        agent that is stuck.
        """
        candidates = await self._lanes_with_pending()
        self._cycles += 1
        self._last_candidates = len(candidates)

        # The patrol lane. A second candidate source on the same cycle: teams
        # whose board has unfinished work and whose lead is due for a sweep.
        # Runs through the same semaphore and the same per-lane lock as message
        # dispatch, so a patrol can never double-run a lead that is already busy
        # in that room — and an empty board yields no candidates at all, which
        # is the feature's whole cost guarantee.
        dispatched_patrols = await self._dispatch_patrols()

        if not candidates:
            return dispatched_patrols

        dispatched = 0
        for agent_id, channel_id in candidates:
            # This lane's previous turn is still going. Rather than wait for it
            # to end (the old `continue`), STEER: push the new messages into the
            # running loop so the agent sees them this turn. Only works if the
            # run registered a SteerChannel (team runs do); otherwise the
            # messages stay queued for the next turn.
            if (agent_id, channel_id) in self._in_flight:
                await self._route_steer(agent_id, channel_id)
                continue
            self._dispatch(agent_id, channel_id)
            dispatched += 1

        if dispatched:
            self._dispatched_total += dispatched
            self._last_dispatch_at = datetime.now(timezone.utc).isoformat()
        return dispatched + dispatched_patrols

    async def _dispatch_patrols(self) -> int:
        """Wake the leads whose boards are due for a sweep. Returns how many.

        Failures are swallowed: the patrol lane is an addition to the poll
        cycle, and a bad sweep must never cost the room its ordinary message
        delivery.
        """
        try:
            from narranexus.platform.message_bus.patrol import teams_due_for_patrol
            from narranexus.platform.utils.db.db_factory import get_db_client

            due = await teams_due_for_patrol(await get_db_client())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[patrol] candidate sweep failed: {e}")
            return 0

        from narranexus.platform.agent_framework.loop.circuit_breaker import should_skip
        from narranexus.platform.message_bus.patrol import mark_patrolled

        count = 0
        for team_id, lead_agent_id, channel_id in due:
            # The lead's turn IN THIS ROOM is still going — a patrol would just
            # queue behind the per-lane lock, and skipping is cheaper than
            # holding a slot. (A lead busy in another team can still be patrolled
            # here — the patrol lane is (lead, this team's room).)
            if (lead_agent_id, channel_id) in self._in_flight:
                continue
            # Same gate message dispatch uses. Without it a lead with a dead key
            # or exhausted quota gets woken every 180-600s, forever, to run a
            # turn that cannot succeed — the exact loop the breaker exists to
            # stop, entered through a lane that did not ask it.
            # No try/except: `should_skip` already fails open internally
            # (returns (False, None) on any read error), so wrapping it would
            # be a handler that can never run — and dead handlers read as
            # "this can throw", which is worse than none.
            cb_gate = await should_skip(lead_agent_id)
            if cb_gate.skip:
                logger.info(
                    f"[patrol] skipping {lead_agent_id} (circuit-breaker: {cb_gate.reason})"
                )
                # The cursor still moves: leaving it stale would make this team
                # a candidate on every single cycle for as long as it is broken.
                try:
                    await mark_patrolled(await get_db_client(), team_id)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[patrol] cursor stamp failed for {team_id}: {e}")
                continue
            self._dispatch_patrol(team_id, lead_agent_id, channel_id)
            count += 1
        return count

    def _dispatch_patrol(self, team_id: str, lead_agent_id: str, channel_id: str) -> None:
        """Spawn one patrol sweep, gated exactly like a message dispatch.

        Same per-lane lock and same semaphore as ``_process_lane``: a lead that
        is already answering in this room must not be woken a second time to
        patrol it, and patrols must not escape the worker cap. Registering in
        ``_in_flight`` under the lane is what makes the next cycle skip this
        lead here — and what makes a stuck patrol visible in the heartbeat like
        any other turn.
        """
        lane = (lead_agent_id, channel_id)

        async def _guarded() -> None:
            lock = self._lane_locks.setdefault(lane, asyncio.Lock())
            async with lock, self._semaphore:
                # Same bookkeeping as `_process_lane`: liveness_snapshot's
                # starvation check and `longest_running_agent` both count only
                # `running` entries, so a patrol that never sets it would hold a
                # worker slot while the heartbeat reported it as merely waiting
                # — the 2026-07-27 shape (33 h of nothing, liveness still green)
                # one lane over.
                flight = self._in_flight.get(lane)
                if flight is not None:
                    flight.running = True
                await self._run_patrol(team_id, lead_agent_id, channel_id)

        task = asyncio.create_task(_guarded())
        self._in_flight[lane] = _InFlight(
            task=task, started_at=time.monotonic()
        )
        # Never a bare create_task: an exception in a fire-and-forget task is
        # only reported during GC (incident lesson #2).
        task.add_done_callback(
            lambda t, ln=lane: self._on_dispatch_done(ln, t)
        )

    def _should_process_message(
        self, msg: BusMessage, agent_id: str, channel_type: str, channel_owner: str,
    ) -> bool:
        """Check if a message should trigger processing for an agent.

        Rules:
        - Never process own messages
        - DM (direct) channels: always process
        - Group channels:
            * Channel owner (created_by) is ALWAYS activated by any new message
            * Other members: only process if mentioned or @everyone
        """
        if msg.from_agent == agent_id:
            return False
        if channel_type == "direct":
            return True
        # Channel owner is always activated, regardless of mentions
        if agent_id == channel_owner:
            return True
        if not msg.mentions:
            return False
        return agent_id in msg.mentions or "@everyone" in msg.mentions

    async def _get_channel_info(self, channel_id: str) -> tuple[str, str]:
        """Get (channel_type, created_by) for a channel."""
        # get_one builds dialect-correct SQL per backend. ``self._bus._db`` is
        # the RAW backend (LocalMessageBus is handed db._backend, not the
        # AsyncDatabaseClient wrapper), so the raw ``execute`` path takes the
        # query verbatim with NO %s→? translation — a MySQL `%s` placeholder
        # threw `near "%"` on SQLite and silently broke bus delivery for every
        # agent that had channel messages (2026-06-09: 影/镜 never received 零's
        # messages). get_one sidesteps the placeholder problem entirely.
        row = await self._bus._db.get_one("bus_channels", {"channel_id": channel_id})
        if row:
            return (
                row.get("channel_type", "group"),
                row.get("created_by", ""),
            )
        return ("group", "")

    def _check_rate_limit(self, agent_id: str, channel_id: str) -> bool:
        """Return True if within rate limit, False if exceeded."""
        key = f"{agent_id}:{channel_id}"
        now = time.monotonic()
        timestamps = self._rate_counters.get(key, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]
        if len(timestamps) >= RATE_LIMIT_MAX:
            logger.warning(
                f"Rate limit exceeded for {agent_id} in channel {channel_id} "
                f"({len(timestamps)}/{RATE_LIMIT_MAX} in {RATE_LIMIT_WINDOW}s)"
            )
            return False
        timestamps.append(now)
        self._rate_counters[key] = timestamps
        return True

    async def _process_lane(self, agent_id: str, channel_id: str) -> bool:
        """Process one lane's pending messages. Returns True if handled.

        A lane is ``(agent_id, channel_id)``. Acquires the per-LANE lock so a
        slow ``_invoke_runtime`` does not let the next poll fire a second
        AgentRuntime for the same pending message (see ``__init__`` for the
        production incident this guards) — while a DIFFERENT lane of the same
        agent runs in parallel.
        """
        # Circuit-breaker skip-gate: a paused (dead key / quota) or cooling
        # agent is skipped entirely — its pending messages are left queued
        # (NOT acked), so they are handled once it resumes. This is what
        # frees the bus from re-triggering a broken agent every poll. Checked
        # before the semaphore so a paused agent doesn't hold a slot.
        #
        # Accepted trade-off: while an agent stays paused (owner hasn't fixed
        # the key yet), its channel backlog accumulates and is drained in one
        # burst on resume. That's intended — dropping/ack'ing messages for a
        # temporarily-broken agent would be silent data loss; the backlog
        # converges once the owner reconfigures and the breaker re-arms.
        from narranexus.platform.agent_framework.loop.circuit_breaker import (
            release_probe,
            should_skip,
            try_begin_probe,
        )
        cb_gate = await should_skip(agent_id)
        if cb_gate.skip:
            logger.debug(
                f"MessageBusTrigger: skipping agent {agent_id} "
                f"(circuit-breaker: {cb_gate.reason})"
            )
            return False

        lock = self._lane_locks.setdefault((agent_id, channel_id), asyncio.Lock())
        async with lock, self._semaphore:
            # Slot acquired — from here the turn counts as `running` rather
            # than `waiting` in the heartbeat. Absent when `_process_lane` is
            # called directly (tests), which is why this is a lookup, not an
            # assumption.
            flight = self._in_flight.get((agent_id, channel_id))
            if flight is not None:
                flight.running = True
            try:
                # This lane owns exactly one channel. Scope the query to it in
                # SQL so the LIMIT falls on THIS room's backlog — not the agent's
                # whole cross-channel backlog filtered to empty in Python, which
                # starves a busy agent's other rooms and burns a slot each poll.
                batch_limit = PENDING_BATCH_LIMIT
                messages = await self._bus.get_pending_messages(
                    agent_id, channel_id=channel_id, limit=batch_limit
                )
                if not messages:
                    return False

                # Skip IM-channel-owned channels — each has its own dedicated
                # trigger that already processed the message; re-consuming
                # would fire AgentRuntime a second time and send duplicate
                # replies. Prefixes derive from the message-source view over the
                # channel registry (see im_channel_prefixes), so a channel is
                # covered the moment its descriptor is registered and drops out
                # the moment the distribution excludes it.
                # STAYS UNTIL THE HISTORICAL ROWS ARE CLEANED UP.
                #
                # `InboxRecorder` (2026-08-17) stopped writing IM turns into
                # the bus tables, so no NEW row can arrive here. But the old
                # rows and memberships are still in place — iron rule #6
                # forbids the destructive migration — and this branch is
                # what has been keeping their `last_processed_at` current.
                #
                # Deleting it now would re-dispatch that history for any
                # agent whose cursor is stale, and the circuit-breaker gate
                # above is exactly how a cursor goes stale: a paused agent
                # never reaches this loop, so its IM channels never got
                # acked. Those turns would then run wearing the Owner-Relay
                # peer prompt — the 2026-07-03 wechat incident, by a second
                # route.
                #
                # Removal is a POST-MIGRATION step, after the Owner's
                # backfill + cleanup. See
                # reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md
                if channel_id.startswith(im_channel_prefixes()):
                    latest = max(messages, key=lambda m: str(m.created_at))
                    await self._bus.ack_processed(agent_id, channel_id, latest.created_at)
                    return False

                channel_type, channel_owner = await self._get_channel_info(channel_id)

                # Mention filtering (channel owner is always activated)
                relevant = [
                    m for m in messages
                    if self._should_process_message(m, agent_id, channel_type, channel_owner)
                ]
                if not relevant:
                    # Still ack to advance cursor
                    latest = max(messages, key=lambda m: str(m.created_at))
                    await self._bus.ack_processed(
                        agent_id, channel_id, latest.created_at
                    )
                    return False

                # A long message sent in parts: hold the lane until the last
                # part lands (no ack — the part's own wake bump brings the loop
                # back), then hand the turn ONE reassembled message. Before
                # this, the wake on part 1 started the turn on a fragment and
                # the later parts read as replies to that answer (multipart.py).
                # `relevant` comes back sorted by created_at with the held
                # tail removed, so `relevant[-1]` below is both the trigger
                # message and the ack high-water — and never past a held row.
                relevant, held = await self._assemble_lane_batch(
                    agent_id, channel_id, messages, relevant, batch_limit,
                    channel_type, channel_owner,
                )
                if held:
                    logger.debug(
                        f"MessageBusTrigger: {channel_id} for {agent_id} — a "
                        f"multipart message is still arriving; "
                        f"{len(relevant)} earlier message(s) go ahead"
                    )
                if not relevant:
                    return False

                # Rate limiting
                if not self._check_rate_limit(agent_id, channel_id):
                    latest = max(relevant, key=lambda m: str(m.created_at))
                    await self._bus.ack_processed(
                        agent_id, channel_id, latest.created_at
                    )
                    return False

                # Half-open probe claim (circuit_breaker.try_begin_probe):
                # only HERE, after every ack-and-return branch above, so the
                # bus poller cannot burn the agent's single probe grant on a
                # batch it was never going to run. A refusal leaves the
                # relevant messages queued (no ack) exactly like the
                # should_skip gate at the top. The gate's read is reused
                # (prior=); the CAS makes its age harmless.
                cb_admission = await try_begin_probe(agent_id, prior=cb_gate)
                if not cb_admission.allowed:
                    logger.debug(
                        f"MessageBusTrigger: not starting turn for {agent_id} "
                        f"(circuit-breaker: {cb_admission.reason})"
                    )
                    return False

                trigger_msg = relevant[-1]
                try:
                    # A won claim rides the turn to the runtime client, which
                    # settles it from the run's outcome (#394 review C1).
                    await self._handle_channel_batch(
                        agent_id, channel_id, relevant, trigger_msg, channel_owner,
                        probe_token=cb_admission.probe_token,
                    )
                finally:
                    # Belt for every exit that never reached that settlement
                    # (the batch threw while building the prompt, the task
                    # was cancelled). No-op once settled: the token CAS no
                    # longer matches.
                    await release_probe(agent_id, cb_admission.probe_token)
                return True
            except Exception as e:
                logger.exception(
                    f"MessageBusTrigger: error processing lane "
                    f"{(agent_id, channel_id)}: {e}"
                )
                return False

    async def _assemble_lane_batch(
        self,
        agent_id: str,
        channel_id: str,
        messages: List[BusMessage],
        relevant: List[BusMessage],
        batch_limit: int,
        channel_type: str,
        channel_owner: str,
    ) -> Tuple[List[BusMessage], bool]:
        """Collapse multipart groups in a lane batch — three explicit steps.

        1. NARROW: assemble what the normal batch holds; a group cut by the
           batch edge is held, never judged on partial evidence (review I2).
        2. WIDE: if nothing before the group can move, re-read the lane with
           `PENDING_BATCH_LIMIT_WIDE` and assemble again.
        3. LAST VERDICT: if even the wide read is cut and still nothing can
           move, assemble once more with ``batch_truncated=False`` so grace
           and supersession rule on what is in view. The LIMIT is per lane,
           so a legal group CAN be cut here when >= PENDING_BATCH_LIMIT_WIDE
           ordinary rows sit between its parts; a group past its grace is
           then delivered as what is in view with a missing-parts marker and
           the rest follows as a second fragment — accepted over holding the
           lane forever (review r2 C2 / r3 I2). A young group is still held.

        Returns ``(deliverable, held)`` exactly as `multipart.assemble` does.
        """
        truncated = len(messages) >= batch_limit
        relevant, held = assemble_parts(relevant, batch_truncated=truncated)
        if not (held and truncated and not relevant):
            return relevant, held
        wide_limit = PENDING_BATCH_LIMIT_WIDE
        wide_messages = await self._bus.get_pending_messages(
            agent_id, channel_id=channel_id, limit=wide_limit
        )
        wide = [
            m for m in wide_messages
            if self._should_process_message(m, agent_id, channel_type, channel_owner)
        ]
        truncated = len(wide_messages) >= wide_limit
        relevant, held = assemble_parts(wide, batch_truncated=truncated)
        if held and truncated and not relevant:
            relevant, held = assemble_parts(wide, batch_truncated=False)
        return relevant, held

    async def _ack_steer_consumed(
        self,
        agent_id: str,
        channel_id: str,
        run_id: str,
        ids: "List[str]",
        latest_created_at: Optional[str],
    ) -> None:
        """Advance the lane's cursors on steer rows the run actually CONSUMED.

        The SteerChannel's ``on_consumed`` is bound to this (via a lambda in
        ``_handle_channel_batch``) and the driver invokes it — in the TURN task,
        while iterating the turn's events — when the loop reports a drain. So it
        shares that task with the turn-end ack (no poll/turn race on the cursors,
        and ``ack_processed`` is forward-only besides). It:

        * marks the ``(run_id, msg_id)`` rows consumed (retention — else the row
          is never eligible for cleanup — and back-pressure accounting);
        * advances the PROCESSING cursor (so the message is not also re-delivered
          as a fresh turn) to the newest consumed message; and
        * raises the flight's ``steered_through`` high-water so the turn-end
          ``_ack_room_seen`` extends the READ cursor to it — the read cursor is
          NOT advanced here, because ``_ack_room_seen`` owns the gap protection
          (``has_unread_before``) that stops an un-rendered backlog below the
          window from being marked read, and a second writer here would bypass
          that gate. One read-cursor writer, one gate.

        The cursor moves on CONSUMPTION, never on push — a message pushed but
        never drained (the turn ended first) is left pending and delivered by a
        fresh turn: never lost, never double.
        """
        if not run_id or not ids:
            return
        try:
            from narranexus.platform.repository.steer_inbox_repository import (
                SteerInboxRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            await SteerInboxRepository(await get_db_client()).mark_consumed_by_msg_ids(
                run_id, ids
            )
            if latest_created_at:
                await self._bus.ack_processed(agent_id, channel_id, latest_created_at)
                # Read cursor is NOT advanced here — hand the high-water to the
                # gap-guarded turn-end writer instead (see docstring).
                flight = self._in_flight.get((agent_id, channel_id))
                if flight is not None and (
                    flight.steered_through is None
                    or latest_created_at > flight.steered_through
                ):
                    flight.steered_through = latest_created_at
        except Exception as e:  # noqa: BLE001 — consumption ack must not break the turn
            logger.warning(
                f"[steer] consumption ack for {(agent_id, channel_id)} run "
                f"{run_id} failed: {type(e).__name__}: {e}"
            )

    async def _discard_steer_orphans(self, run_id_cell: "List[str]") -> None:
        """Reclaim steer_inbox rows this run pushed but never drained, at run
        teardown. ``run_id_cell`` is a one-element late-bind holder — the run id
        is not known until Step 0 stamps it into ``run_id_cell[0]`` (same shape
        as ``watched_run_id``). Best-effort — a cleanup failure must never break
        the turn. Runs at AsyncExitStack unwind AFTER release (registered before
        it, LIFO), so no ``_route_steer`` can still find the run to append, and
        after the loop's last drain, so it cannot race an in-flight
        ``deliver_consumed``. Empty id (run never bound) → no steer rows, nothing
        to reclaim."""
        run_id = run_id_cell[0]
        if not run_id:
            return
        try:
            from narranexus.platform.repository.steer_inbox_repository import (
                SteerInboxRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            n = await SteerInboxRepository(await get_db_client()).discard_run(run_id)
            if n:
                logger.info(f"[steer] discarded {n} never-drained rows for run {run_id}")
        except Exception as e:  # noqa: BLE001 — teardown cleanup never breaks a turn
            logger.warning(
                f"[steer] discard orphans for run {run_id} failed: "
                f"{type(e).__name__}: {e}"
            )

    async def _route_steer(self, agent_id: str, channel_id: str) -> None:
        """DELIVER a running lane's new messages into its live run's context.

        No-op if the lane's run is not steerable — no registered SteerChannel
        (a peer DM, or the tiny window before the run's id binds). Those
        messages stay queued and dispatch as a fresh turn once the lane frees.

        DELIVERY ONLY — this NEVER advances the bus cursor. A ``push`` onto the
        SteerChannel queue is not proof the run READ it (the turn may end before
        its next drain), so advancing the cursor here would silently lose a
        pushed-but-never-drained message. Instead the cursor moves only when the
        run reports the row CONSUMED (drained into its context), through
        ``steer_channel.on_consumed`` (wired in ``_handle_channel_batch``). So an
        undelivered or un-drained message is simply left pending and delivered by
        a fresh turn — never lost, and never double (the steer_inbox dedup key is
        ``(run_id, msg_id)``, and a consumed row's cursor already moved past it).
        Any failure leaves the batch queued and is swallowed.
        """
        from narranexus.platform.agent_runtime.run_registry import get_run_registry

        run = get_run_registry().live_run(agent_id, channel_id)
        if run is None:
            return
        # Steer ONLY messages newer than what the running turn already rendered.
        # Its trigger batch (<= this high-water) is already in its prompt; its own
        # end-ack advances the cursor past it. The turn declares the high-water
        # before the run becomes steerable, so a live run has it — the rare None
        # gap is safe to skip (messages stay queued, never lost).
        flight = self._in_flight.get((agent_id, channel_id))
        watermark = flight.rendered_through if flight is not None else None
        if watermark is None:
            return
        # Mark this lane as having a steer cycle in flight for the whole method.
        # The turn-end `_ack_room_seen` treats an in-flight cycle exactly like an
        # unsteered_gap: while a `_route_steer` is running it CANNOT prove there
        # is no un-rendered message between the window and steered_through (it may
        # be about to discover one after its awaits), so it must not extrapolate
        # the read cursor to steered_through. finally-decremented: an exception
        # path that skipped the -1 would pin this lane's read cursor at the
        # trigger high-water forever (a perceivable regression, not just a leak).
        flight.steer_cycles_in_flight += 1
        try:
            # Scope to this lane's room in SQL (same reason as _process_lane):
            # the LIMIT must land on this room, not the agent's cross-channel
            # backlog. Then keep only what is NEWER than what the turn rendered.
            pending = await self._bus.get_pending_messages(
                agent_id, channel_id=channel_id
            )
            messages = [m for m in pending if canonical_ts(m.created_at) > watermark]
            if not messages:
                return
            channel_type, channel_owner = await self._get_channel_info(channel_id)
            relevant = [
                m for m in messages
                if self._should_process_message(m, agent_id, channel_type, channel_owner)
            ]
            relevant_ids = {m.message_id for m in relevant}
            unaddressed = [m for m in messages if m.message_id not in relevant_ids]

            # Raise the read-cursor gate flag for any un-steered message BEFORE
            # the release re-check below. This is a pure in-memory CONSERVATIVE
            # marker (not a DB write, not an ack) — it only makes the read cursor
            # hold further back — so it must survive an early return, or the
            # release race would silently drop this gate input and let
            # `_ack_room_seen` extrapolate past a never-rendered message.
            if unaddressed:
                flight.unsteered_gap = True

            # Re-confirm the run is STILL the same live handle before writing
            # anything (append / push / ack). The awaits above give the turn time
            # to end and release; an append after the run's orphan reclaim +
            # release would write a row nobody will ever drain. Compare handle
            # IDENTITY, not is_alive: the lane task stays alive past release, so a
            # liveness probe would wrongly say "still here". Mismatch → perform
            # NEITHER the DB writes NOR the acks; the messages stay pending for a
            # fresh turn. This narrows the append race (the get_db_client +
            # inbox.append below still yield); the rare leftover row is reclaimed
            # by the daily steer_inbox retention tick's unconsumed-orphan arm
            # (_maybe_run_steer_cleanup, wired in start()'s poll loop).
            if get_run_registry().live_run(agent_id, channel_id) is not run:
                return

            if relevant:
                from narranexus.platform.repository.steer_inbox_repository import (
                    SteerInboxFull,
                    SteerInboxRepository,
                )
                from narranexus.platform.schema.steer_schema import SteerInjection
                from narranexus.platform.utils.db.db_factory import get_db_client

                inbox = SteerInboxRepository(await get_db_client())
                try:
                    for m in relevant:
                        inj = SteerInjection(
                            run_id=run.run_id, msg_id=m.message_id, role="user",
                            content=m.content, sender_id=m.from_agent, source="team",
                        )
                        # steer_inbox is the durable record AND the dedup: push
                        # into the loop only on a NEW row, so a message re-seen
                        # before it is consumed never double-injects. Remember its
                        # CANONICAL created_at (not str(datetime), which is
                        # space-format and sorts BELOW every 'T' cursor) so the
                        # consumption ack advances the cursor correctly.
                        if await inbox.append(inj):
                            await run.steer.push(inj)
                            run.steer.remember(m.message_id, canonical_ts(m.created_at))
                except SteerInboxFull:
                    # The run's UNCONSUMED backlog is at capacity — it drains
                    # slower than messages arrive. Stop; what we pushed is
                    # delivered (its cursor moves on consumption), the rest stay
                    # queued for a later cycle. No cursor to unwind — delivery is
                    # push, the cursor is consumption. Back-pressure, not loss.
                    logger.warning(
                        f"[steer] inbox full for {(agent_id, channel_id)}, rest queued"
                    )

            if unaddressed and flight is not None:
                # unsteered_gap was already raised above (before the re-check).
                # Here we only advance the PROCESSING cursor over un-addressed
                # messages strictly OLDER than the oldest UN-consumed steered
                # message (the floor), so a busy room's chatter does not
                # permanently occupy the LIMIT-50 window and starve live-steering
                # (review Important #1).
                # Never past the floor (that would mark an un-consumed steered
                # message processed before the run read it), and NEVER ack_read
                # (un-addressed messages were never rendered — the 🔴1 red line).
                # Re-fetch the floor now (after this cycle's deliveries, and the
                # turn task may have consumed a batch between the awaits above).
                floor = run.steer.oldest_unconsumed_created_at()
                ackable = [
                    m for m in unaddressed
                    if floor is None or canonical_ts(m.created_at) < floor
                ]
                if ackable:
                    latest = max(ackable, key=lambda m: canonical_ts(m.created_at))
                    await self._bus.ack_processed(
                        agent_id, channel_id, latest.created_at
                    )
        except Exception as e:  # noqa: BLE001 — a steer failure must not break the loop
            logger.warning(
                f"[steer] route into live run for {(agent_id, channel_id)} "
                f"failed, messages left queued: {type(e).__name__}: {e}"
            )
        finally:
            # Always drop the in-flight marker — a missed decrement would pin this
            # lane's read cursor at the trigger high-water for the rest of the
            # turn (a perceivable regression, not a leak).
            flight.steer_cycles_in_flight -= 1

    async def _maybe_run_steer_cleanup(self) -> None:
        """Reclaim old steer_inbox rows, delivery receipts and owner-notice
        cooldowns — startup + once a day. This trigger is each table's only
        production writer, so it owns the retention tick (else the tables are
        write-only and grow forever). Gated on monotonic time; best-effort — a
        cleanup failure never touches the poll loop."""
        now = time.monotonic()
        if now - self._last_steer_cleanup_monotonic < STEER_CLEANUP_INTERVAL_S:
            return
        self._last_steer_cleanup_monotonic = now
        try:
            from narranexus.platform.repository.steer_inbox_repository import (
                SteerInboxRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            deleted = await SteerInboxRepository(db).cleanup_older_than_days(
                STEER_RETENTION_DAYS, STEER_ORPHAN_DAYS
            )
            if deleted:
                logger.info(f"[steer-inbox] retention swept {deleted} rows")
        except Exception as e:  # noqa: BLE001 — retention never breaks the loop
            logger.warning(f"[steer-inbox] retention sweep failed: {type(e).__name__}: {e}")
        # The same tick sweeps the two ledgers this trigger writes (2026-09-09):
        # each is best-effort on its own so one failing sweep never starves
        # the others.
        try:
            from narranexus.platform.repository.bus_delivery_receipt_repository import (
                BusDeliveryReceiptRepository,
            )
            from narranexus.platform.repository.owner_notice_cooldown_repository import (
                OwnerNoticeCooldownRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            swept = await BusDeliveryReceiptRepository(db).cleanup_older_than_days(
                RECEIPT_RETENTION_DAYS
            )
            swept += await OwnerNoticeCooldownRepository(db).cleanup_older_than_days(
                NOTICE_COOLDOWN_RETENTION_DAYS
            )
            if swept:
                logger.info(f"[bus-ledgers] retention swept {swept} rows")
        except Exception as e:  # noqa: BLE001 — retention never breaks the loop
            logger.warning(f"[bus-ledgers] retention sweep failed: {type(e).__name__}: {e}")

    async def _get_agent_owner(self, agent_id: str) -> Optional[str]:
        """Look up the owner user_id for an agent. Returns "" when the agent
        is unknown and None when the LOOKUP failed (resolve_owner's split) —
        every consumer gates on truthiness, so both degrade the same way.
        Delegates to the shared AgentRepository.resolve_owner seam."""
        try:
            from narranexus.platform.repository.agent_repository import AgentRepository
            from narranexus.platform.utils.db.db_factory import get_db_client
            return await AgentRepository(await get_db_client()).resolve_owner(agent_id)
        except Exception as e:
            logger.warning(f"_get_agent_owner({agent_id}) failed: {e}")
            return ""

    async def _ack_room_seen(
        self, agent_id: str, channel_id: str, trigger_message: BusMessage,
        is_team: bool, rendered_from: Optional[str],
    ) -> None:
        """Advance the READ cursor for a team room whose turn actually ran.

        A team room delivers by RENDERING: `_build_team_prompt` puts the room's
        recent scrollback into the turn's user message, so what the agent has
        been shown is exactly that window. That is what "read" means here, and
        it holds whether the agent replied or stayed silent — silence is a
        reply-discipline decision, not a claim that it did not look.

        `rendered_from` is the oldest message the prompt actually carried. The
        cursor may only pass it when the window reaches back to wherever the
        cursor already was — otherwise there is a GAP of messages this turn
        never showed, and a single high-water cursor cannot say "read the window
        but not the gap below it". It would swallow them.

        That gap is reachable and not rare: a member nobody @mentions for a
        while accumulates a backlog, and the day it finally gets @mentioned the
        prompt still only carries `TEAM_HISTORY_LIMIT` messages. Advancing to
        the trigger would mark the rest read having never rendered them — the
        same silent loss this method refuses to cause at the un-mentioned and
        rate-limited ack sites, arriving through the one path that does run a
        turn.

        So on a gap the cursor simply does not move. The room stays behind and
        keeps surfacing through the unread list, which is where an un-caught-up
        member is supposed to see it. Rooms whose backlog fits the window — the
        ordinary case — still converge in one turn.

        Nothing else advanced this cursor. Its only other writer keys off a bus
        delivery tool showing up in the turn's trace, and a team reply is posted
        by this trigger — server-side, no tool call — so the cursor sat at
        `joined_at` for the life of the agent while every team message stayed
        unread and rode into EVERY scenario's context, owner chat included.

        Deliberately NOT called from the two ack sites in `_process_lane`
        (un-mentioned, rate-limited). Those advance `last_processed_at` without
        running a turn, so nothing was rendered and nothing was seen. Marking
        them read would drop the messages unseen — and would take with them the
        only way a member nobody @mentioned ever learns what its room is doing,
        which is a capability, not an oversight.

        Team rooms only. In a DM the unread list IS the queue: "I will get to
        it" depends on the message resurfacing, so only an actual reply clears
        it and that path runs through the module hook.

        Best-effort — a cursor that fails to advance costs some duplicate
        context next turn; raising here would cost the turn itself.
        """
        if not is_team or not rendered_from:
            return
        try:
            # "Did the window reach the bottom of what this agent still owes?"
            # An existence question, asked as one: the unread predicate already
            # measures against the cursor, so anything left below the window is
            # by definition something this turn did not render. Correct whether
            # or not a cursor exists — and it stays a `LIMIT 1`, rather than
            # dragging the agent's whole cross-channel backlog over the wire to
            # be filtered in Python, which is the shape this lane just spent a
            # PR removing.
            if await self._bus.has_unread_before(
                agent_id, channel_id, rendered_from
            ):
                logger.info(
                    f"[bus] read cursor held for {agent_id} in {channel_id}: "
                    f"unread messages predate this turn's scrollback and were "
                    f"never rendered"
                )
                return
            # Upper bound: the trigger high-water, EXTENDED to the newest steered
            # message the run consumed this turn — those were rendered (injected)
            # too, so they are read. But only when nothing UN-steered slipped in
            # between (unsteered_gap): a new message that was neither in the
            # scrollback nor steered sits below steered_through and was never
            # shown, so on a gap we fall back to the trigger high-water and leave
            # it unread. The read cursor still has ONE writer and ONE gate.
            up_to = canonical_ts(trigger_message.created_at)
            flight = self._in_flight.get((agent_id, channel_id))
            if (
                flight is not None
                and flight.steered_through is not None
                # Extrapolate ONLY when we can PROVE there is no un-rendered gap:
                # no un-steered message seen (unsteered_gap) AND no _route_steer
                # cycle still running (which might discover one after its awaits).
                and not flight.unsteered_gap
                and flight.steer_cycles_in_flight == 0
                and flight.steered_through > up_to
            ):
                up_to = flight.steered_through
            await self._bus.ack_read(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=up_to,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[bus] could not advance read cursor for {agent_id} "
                f"in {channel_id}: {e}"
            )

    async def _handle_channel_batch(
        self,
        agent_id: str,
        channel_id: str,
        messages: List[BusMessage],
        trigger_message: BusMessage,
        channel_owner: str = "",
        probe_token: Optional[str] = None,
    ) -> None:
        """
        Handle a batch of messages from a single channel for an agent.

        ``probe_token`` is the circuit-breaker half-open probe claim
        ``_process_lane`` won for this turn (None for an ordinary turn); it
        is handed to ``_invoke_runtime`` so the runtime client settles it.

        Builds a prompt, invokes AgentRuntime, and on success advances the
        processing cursor. On failure, records the failure for retry tracking.

        Team group chat (``channel_owner`` is the synthetic ``team_<id>``
        marker) is a distinct surface: the agent gets a group-chat prompt
        (not the owner-relay), and its reply is posted BACK INTO the channel
        — with any @mentions parsed so teammates get pulled in — so the user
        and teammates all see it in the shared room. Every other channel
        (peer DM, IM bridges) keeps the original owner-relay + inbox path.
        """
        # Imported here, not at module scope: `agent_runtime` pulls in
        # `module`, which imports back into this package — the same circular
        # import that keeps `get_agent_runtime_client` local to
        # `_invoke_runtime`.
        from narranexus.platform.agent_runtime.cancel_watcher import get_cancel_watcher
        from narranexus.platform.agent_runtime.cancellation import (
            CancellationToken,
            CancelledByUser,
        )

        is_team = channel_owner.startswith(TEAM_ROOM_OWNER_PREFIX)
        # The oldest message the prompt ends up carrying. Declared here, not in
        # the team branch, so the cancelled handler can read it however the body
        # exits — the read cursor may not pass what was never rendered.
        rendered_from: Optional[str] = None
        member_map: Dict[str, str] = {}
        # DM branch overwrites this with the classifier's verdict; team rooms
        # keep False (they never carry the Owner-Relay/Answer-the-peer split).
        errand_continuation = False
        # Flipped by the room-post handler below. Separate from `_hop_done`
        # only in that it names WHY the hop did not complete.
        posted = True

        # Declare this turn's rendered high-water BEFORE the run can become
        # steerable (on_event_id fires later, inside _invoke_runtime). trigger
        # is the NEWEST batched message, so it is the exact boundary the ack at
        # turn end uses; recording it now lets `_route_steer` inject only gen.-
        # uinely NEW messages and never the batch this turn already holds. Guard
        # the lookup: patrol / tests may reach this without a dispatch flight.
        _flight = self._in_flight.get((agent_id, channel_id))
        if _flight is not None:
            # canonical_ts (not str(datetime)) so the steer watermark compares in
            # the same 'T'-ISO format the cursors use — a space-format string
            # sorts below every real created_at (the hazard canonical_ts exists
            # for), which would make the filter either steer nothing or re-steer
            # the batch.
            _flight.rendered_through = canonical_ts(trigger_message.created_at)

        # Hop timing ([bus-timing], 2026-08-05): the 2026-08-01 event clocked
        # a bus hop at 45-95s with no way to split "sat in the queue" from
        # "the turn itself". queue_wait = TRIGGER message insert -> this
        # dispatch (bounded by the adaptive poll, 3-12s; the trigger is the
        # NEWEST batched message, so this is a lower bound on user-perceived
        # wait — oldest_wait is the upper bound); turn = the runtime call;
        # hop closes when the AGENT'S REPLY is DELIVERED (team room: our post
        # below; DM: the agent's own bus_send fires mid-turn, so turn covers
        # it). A platform line — a failure notice, an "it said nothing" notice
        # — is not a delivery and closes no hop, in either lane.
        # Companion of the runtime's [turn-timing] line, which splits the
        # turn body further. Measurement first — Base recvrdLPavdQgU.
        # Timestamp parsing goes through the bus package's own _as_utc —
        # the one parser every bus timestamp comparison already uses.
        _t_dispatch = time.monotonic()
        _now_utc = datetime.now(timezone.utc)

        def _wait_s(raw) -> float:
            parsed = _as_utc(raw)
            return (
                max(0.0, (_now_utc - parsed).total_seconds())
                if parsed else -1.0
            )

        _queue_wait_s = _wait_s(trigger_message.created_at)
        _oldest_wait_s = max(
            (_wait_s(m.created_at) for m in messages), default=_queue_wait_s
        )
        _turn_s = -1.0
        _hop_done = False
        try:
            if is_team:
                roster = await self._team_roster(channel_id)
                # Still needed downstream: @mention parsing works on names.
                member_map = {
                    r["agent_id"]: r.get("name") or r["agent_id"] for r in roster
                }
                team_owner = await self._get_agent_owner(agent_id)
                team_id = channel_owner[len(TEAM_ROOM_OWNER_PREFIX):]
                # Feed the recent room scrollback (not just the @mention batch)
                # so the agent sees a shared file/image posted earlier by anyone
                # and can Read it — no manual relay. `messages` (the @mentions
                # for THIS agent) still marks what it should respond to.
                history = await self._bus.get_recent_messages(channel_id, limit=TEAM_HISTORY_LIMIT)
                # Raise the steer watermark to the NEWEST message the scrollback
                # actually shows (not just the trigger @mention). A room message
                # that arrived during the DB round-trips since entry is now in the
                # prompt's scrollback; without this it would ALSO be steered in,
                # so the agent would see it twice in one turn. Anything already
                # rendered is <= the watermark and never re-steered.
                if _flight is not None and history:
                    newest = max(canonical_ts(m.created_at) for m in history)
                    if newest > _flight.rendered_through:
                        _flight.rendered_through = newest
                bulletin = await self._load_bulletin(team_id)
                rendered_from = history[0].created_at if history else None
                lead_agent_id, work_items, team_row = await self._team_board(team_id)
                prompt = self._build_team_prompt(
                    agent_id, history, roster,
                    owner_user_id=team_owner, team_id=team_id,
                    trigger_messages=messages,
                    bulletin=bulletin,
                    lead_agent_id=lead_agent_id,
                    work_items=work_items,
                    team=team_row,
                )
            else:
                # Owner lookup up-front — used by both the prompt (to remind the
                # agent its owner is waiting in chat) and the inbox writer.
                owner_user_id = await self._get_agent_owner(agent_id)
                # Resolve the owner's human name for the relay prose (the raw
                # user_id stays as the notify_owner routing key).
                owner_name = ""
                if owner_user_id:
                    from narranexus.platform.utils.db.db_factory import get_db_client
                    from narranexus.platform.repository import UserRepository
                    owner_name = await UserRepository(await get_db_client()).get_display_name(owner_user_id)

                # Which directive applies depends on who started this thread:
                # a reply to OUR errand goes to the owner; a fresh question
                # from a peer must be answered on the bus. Getting this wrong
                # is what made recipients answer their owner and leave the
                # asking agent hanging (P1 2026-08-03).
                i_started = await self._incoming_is_reply_to_my_errand(
                    agent_id, channel_id, messages
                )
                errand_continuation = i_started

                # Build prompt from messages
                prompt = self._build_prompt(
                    messages,
                    owner_user_id=owner_user_id,
                    owner_name=owner_name,
                    i_started_this_exchange=i_started,
                )

            logger.info(
                f"MessageBusTrigger: triggering agent {agent_id} "
                f"for channel {channel_id} ({len(messages)} messages, team={is_team})"
            )

            # Team rooms mirror live "what is this agent doing" into
            # bus_agent_activity so the team-chat UI can show running/phase/
            # elapsed/steps (the bus path has no WS stream). Only for team
            # channels. `turn()` owns the start row, the timer heartbeat and
            # the idle flip, whichever way the body exits.
            async with contextlib.AsyncExitStack() as stack:
                on_progress = None
                note_event_id = None
                if is_team:
                    from narranexus.platform.utils.db.db_factory import get_db_client
                    from narranexus.platform.message_bus import _bus_activity
                    act = await stack.enter_async_context(
                        _bus_activity.turn(await get_db_client(), agent_id, channel_id)
                    )
                    on_progress = act.on_progress
                    note_event_id = act.note_event_id

                # A stop request for this run arrives through the DB (the
                # owner's click lands in the backend process, not here), so the
                # token needs a watcher to fire it. Registration can only
                # happen once the run HAS an id — Step 0 mints it and
                # `on_event_id` is the first moment it exists. Registered for
                # every bus run, not just team ones: the endpoint is
                # run-scoped, so a future surface should be able to stop a
                # peer-DM turn too.
                from narranexus.platform.utils.db.db_factory import get_db_client

                cancellation = CancellationToken()
                watcher = get_cancel_watcher(await get_db_client())
                watched_run_id: list[str] = [""]
                # Unregister however the body exits — a token left behind
                # keeps the poll loop alive for a run that is gone.
                stack.callback(lambda: watcher.unregister(watched_run_id[0]))

                # Live steering: a team run is steerable — a message that arrives
                # while it runs is pushed into its loop instead of waiting for a
                # fresh turn. The SteerChannel is created now (so `_invoke_runtime`
                # hands it to the loop) and registered in the RunRegistry once the
                # run has an id (mirroring the cancel watcher). Released however
                # the body exits, so the poll loop never steers into a gone run.
                from narranexus.platform.agent_runtime.run_registry import (
                    get_run_registry,
                )
                from narranexus.platform.agent_runtime.steer_channel import SteerChannel

                # agent_id/channel_id are known now; run_id is late-bound (Step
                # 0), so the channel carries them for the overflow warning and the
                # consumption callback, and gets its run_id stamped in
                # on_event_id — the identity the mechanism asks the orchestrator
                # to supply (see steer_channel mirror).
                steer_channel = (
                    SteerChannel(agent_id=agent_id, channel_id=channel_id)
                    if is_team else None
                )
                steer_run_id: list[str] = [""]
                # Orphan reclaim MUST run AFTER release (once the run is out of
                # the RunRegistry, no _route_steer can find it to append a new
                # row). AsyncExitStack is LIFO, so to run discard AFTER release it
                # must be registered BEFORE release — registered here, first, it
                # unwinds last. (It still runs at unwind, strictly after the loop
                # ended and every deliver_consumed fired, so it never races an
                # in-flight drain; the DELETE awaits, hence push_async_callback.)
                if steer_channel is not None:
                    stack.push_async_callback(
                        self._discard_steer_orphans, steer_run_id
                    )
                stack.callback(
                    lambda: steer_channel is not None
                    and get_run_registry().release(steer_run_id[0])
                )

                # When the run reports draining steer rows, advance the cursors on
                # CONSUMPTION (see `_ack_steer_consumed`). Bound to this lane and
                # to the run id at call time (steer_run_id[0] is set at Step 0).
                if steer_channel is not None:
                    steer_channel.on_consumed = (
                        lambda ids, latest: self._ack_steer_consumed(
                            agent_id, channel_id, steer_run_id[0], ids, latest
                        )
                    )

                async def on_event_id(run_id: str) -> None:
                    watched_run_id[0] = run_id
                    watcher.register(run_id, cancellation)
                    if steer_channel is not None:
                        steer_run_id[0] = run_id
                        steer_channel.run_id = run_id  # label the overflow warning
                        # Liveness backstop the mechanism requires of every
                        # producer (not optional — see run_registry mirror): if
                        # this lane's task dies WITHOUT the stack.callback release
                        # running (a hard crash the finally cannot cover),
                        # live_run sweeps the dead run so the surface is not deaf
                        # forever. on_event_id runs in the lane's own task, so
                        # current_task() is exactly that task; `not done()` holds
                        # for the whole turn. A wrong "dead-while-alive" would
                        # silently drop steering, so probe the lane task itself —
                        # nothing looser.
                        run_task = asyncio.current_task()
                        get_run_registry().register(
                            agent_id, channel_id, run_id, steer_channel,
                            is_alive=(lambda t=run_task: t is not None and not t.done()),
                        )
                    if note_event_id is not None:
                        await note_event_id(run_id)

                # No deliverer any more.
                #
                # A team reply used to be the agent's PLAIN TEXT, posted here by
                # `_deliver_reply` from inside the turn. That made this the one
                # surface in the system where "plain text reaches nobody" was
                # false, and the exception propagated: the framework
                # constitution, ChatModule's instructions and this module's rules
                # all assert the general rule, and only one of the three could be
                # switched off per turn. Six review rounds on PR #311 went to the
                # contradictions that grew out of it.
                #
                # The room now takes a tool call (`message_team`) like every
                # other surface, so what the trigger has to know shrinks to ONE
                # question — did the agent post here this turn? — and the bus can
                # answer it (`has_message_from_turn`). The @mention parsing, the
                # hop cap and the cap's narration moved to `team_posting`, where
                # they are properties of posting into a room rather than of the
                # trigger that used to own delivery.
                # Call AgentRuntime. Pass a clean retrieval anchor (peer bodies
                # only, no Owner-Relay boilerplate) for narrative routing — the
                # execution `prompt` is far noisier. See 2026-06-01 design.
                turn = await self._invoke_runtime(
                    agent_id=agent_id,
                    sender_agent_id=trigger_message.from_agent,
                    prompt=prompt,
                    channel_id=channel_id,
                    trigger_message_id=trigger_message.message_id,
                    retrieval_anchor=build_bus_anchor(messages),
                    errand_continuation=errand_continuation,
                    on_progress=on_progress,
                    on_event_id=on_event_id,
                    cancellation=cancellation,
                    steering=steer_channel,
                    # The half-open probe claim, if this turn holds it; the
                    # runtime client settles it (None: ordinary turn).
                    probe_token=probe_token,
                    # No monologue harvest on a reply turn: a team reply is a
                    # tool call (`message_team`) now, so `include_monologue`
                    # stays False here and is passed only by the patrol path.
                    # See `_invoke_runtime`'s docstring for why patrol is the
                    # one exception.
                    # The tree this turn continues, as stamped on the message
                    # that woke it. Empty for a user's message (this run then
                    # becomes a root) — see the recorder's bind.
                    root_run_id=trigger_message.root_run_id or "",
                    # The turn's team, for the MCP identity headers — tools
                    # must learn it from the server, never from a model
                    # parameter (see module/_mcp_identity.py).
                    team_id=team_id if is_team else "",
                    # The team-room marker for this turn. It rides
                    # trigger_extra_data so MessageBusModule can read it:
                    # expressive_tools points the reply reminder at
                    # `message_team` (not the peer `message_agent`). It no longer
                    # drops the peer verb — every internal send verb stays
                    # reachable on every turn (capability follows the agent). So
                    # dropping this marker only flips the reminder's default to
                    # the peer verb; it changes what a plain reply targets, not
                    # what the agent can reach.
                    team_room=is_team,
                )

            _turn_s = time.monotonic() - _t_dispatch

            # On success: advance cursor
            await self._bus.ack_processed(
                agent_id=agent_id,
                channel_id=channel_id,
                up_to_timestamp=trigger_message.created_at,
            )
            await self._ack_room_seen(
                agent_id, channel_id, trigger_message, is_team, rendered_from
            )

            logger.info(
                f"MessageBusTrigger: agent {agent_id} processed "
                f"{len(messages)} messages in channel {channel_id}"
            )

            if turn.text:
                if is_team:
                    # ONE question now: did the agent put anything in this room
                    # during this turn? The room is a tool call, so the answer is
                    # a fact in the bus rather than the outcome of a callback the
                    # trigger owned — `has_message_from_turn` matches
                    # `bus_messages.event_id` against this turn's id.
                    #
                    # Deliberately NOT `turn.delivered`: that counts
                    # `notify_owner` too, and an agent that only
                    # told its owner has left this room silent — precisely the
                    # case the notice below is for.
                    # Can we even JUDGE "did it reach the room"? The judge is an
                    # event_id identity join, and event_id rides an MCP request
                    # header that is legitimately absent sometimes
                    # (`caller_event_id_from_request`: "None is normal ... must
                    # never fail a registration"; identity is never flow control).
                    can_judge = bool(turn.event_id)
                    spoke = (
                        await self._bus.has_message_from_turn(
                            channel_id, agent_id, turn.event_id
                        )
                        if can_judge
                        else False
                    )
                    # When we cannot judge, assume the reply landed. A false
                    # "never sent it" notice posted UNDER a message that IS in the
                    # room is the worse harm — and treating the miss as real also
                    # made _hop_done undercount delivery. A CONFIRMED miss needs
                    # event_id present AND the join empty (the elif below).
                    posted = spoke or not can_judge

                    if turn.fatal:
                        # `turn.text` is a failure notice, not the agent's words.
                        # Nothing else would reach the room, so a teammate that
                        # @mentioned this agent could not tell "not interested"
                        # from "broken" — the hand-off just stops. Posted as the
                        # ROOM: it is not a reply the agent made, and routing it
                        # through the normal path would parse @mentions and drag
                        # teammates into somebody else's failure.
                        #
                        # Announced whether or not the agent spoke: this notice
                        # claims the TURN failed, which is true either way, and a
                        # room whose agent spoke and then broke needs the second
                        # half stated.
                        try:
                            await self._post_to_room(
                                from_agent=channel_owner,
                                to_channel=channel_id,
                                content=turn.text,
                            )
                        except Exception as e:  # noqa: BLE001
                            # Swallowed — a notice must not take the turn down —
                            # but never silently: this is the room's only window
                            # onto a broken agent, and a version that fails
                            # without a trace makes "the room went quiet" two
                            # indistinguishable causes.
                            logger.warning(
                                f"[team-room] could not post failure notice in "
                                f"{channel_id}: {e}"
                            )
                    elif not spoke and can_judge:
                        # The turn produced text and none of it reached the room.
                        # Under the old contract this was the platform's post
                        # failing; now it is the agent not having called
                        # `message_team` — the 2026-08-01 briefing-squad shape,
                        # which this surface was structurally immune to while
                        # plain text auto-posted and is exposed to again.
                        #
                        # The platform does NOT write the reply for it (binding
                        # rule #15 — we do not police what the model does), but
                        # the loss must stop being silent, and the text is kept
                        # for the owner's inbox.
                        await self._announce_failed_room_post(
                            agent_id, channel_id, trigger_message, turn,
                            "the turn produced a reply but never sent it to "
                            "the room",
                        )
                    elif not spoke:
                        # Undecidable, not a confirmed miss: no event_id header
                        # this turn, so the identity join cannot run. Announcing
                        # here is exactly the false ⚠️ this branch must not emit.
                        logger.debug(
                            f"[team-room] cannot confirm delivery for {agent_id} "
                            f"in {channel_id} (no event_id this turn) — assuming "
                            f"posted, not announcing"
                        )
                    # The cap's narration is NOT here any more: `team_posting`
                    # posts it, because it is the thing that applied the cap.
                    # Narrating from both places said it twice.
                else:
                    # Write response to inbox
                    await self._write_to_inbox(
                        agent_id, channel_id, trigger_message, turn.text
                    )
                    # The sender's receipt: its message was acted on, but the
                    # action was a relay to OUR owner, not a reply to it. A
                    # peer that also got a tool reply reads `processed`.
                    await self._stamp_receipts(
                        messages, agent_id, channel_id,
                        RECEIPT_PROCESSED if turn.delivered else RECEIPT_RELAYED,
                    )
            elif turn.reached_nobody:
                # `reached_nobody` IS "no text and no tool reached anyone", so
                # the only thing this channel gets is the platform's own line
                # saying the agent said nothing. A notice is not a delivery.
                posted = False
                await self._announce_undelivered_turn(
                    agent_id, channel_id, trigger_message,
                    is_team=is_team, errand_continuation=errand_continuation,
                    batch=messages,
                )
                if not is_team:
                    await self._stamp_receipts(
                        messages, agent_id, channel_id, RECEIPT_SILENT
                    )
            elif not is_team:
                # No owner-facing text, but a tool reached someone: the peer
                # was answered (or a third party was), which is the receipt
                # the sender is waiting on.
                await self._stamp_receipts(
                    messages, agent_id, channel_id, RECEIPT_PROCESSED
                )

            # A reply that never got out is not a completed hop: [bus-timing]
            # measures delivery, and counting a lost or never-attempted one
            # would flatter the series.
            _hop_done = posted

        except CancelledByUser as e:
            # `_hop_done` deliberately stays False: a stopped turn is not a
            # completed hop, and letting it into the [bus-timing] series would
            # mix "how long delivery takes" with "when the owner pressed stop".
            # A stop is a user decision, not a fault. Three things must NOT
            # happen here, each of which the generic handler below would do:
            #
            #  1. `record_failure` — three "failures" poison the message and
            #     `get_pending_messages` filters it out FOREVER. Stopping a
            #     run three times would make that message undeliverable.
            #  2. the owner-facing permanent-failure notice — telling someone
            #     their agent broke when they pressed stop themselves.
            #  3. failure alerting / retry accounting downstream.
            #
            # And one thing that must happen and has no other home: ADVANCE
            # THE CURSOR. On the success path the ack sits at the END of the
            # try block, so an exception skips it — the message would still be
            # pending, the next poll would start the very run the owner just
            # stopped, and stop would read as "it restarted itself".
            logger.info(
                f"MessageBusTrigger: run stopped by owner — agent {agent_id}, "
                f"channel {channel_id} ({e.reason or 'no reason given'})"
            )
            with contextlib.suppress(Exception):
                await self._bus.ack_processed(
                    agent_id=agent_id,
                    channel_id=channel_id,
                    up_to_timestamp=trigger_message.created_at,
                )
                # Stopping does not un-show what was already shown: the prompt
                # was built and the room's scrollback rendered before the model
                # ever got a chance to be interrupted.
                await self._ack_room_seen(
                    agent_id, channel_id, trigger_message, is_team, rendered_from
                )
        except Exception as e:
            logger.exception(
                f"MessageBusTrigger: failed to process channel {channel_id} "
                f"for agent {agent_id}: {e}"
            )
            # Record the failure on EVERY row the trigger message stands for.
            # A reassembled multipart message carries part 1's identity, and
            # the poison filter in `get_pending_messages` is per ROW: counting
            # on part 1 alone dropped it and left parts 2..N pending as a
            # headless group — held for the grace, delivered as a fragment
            # claiming part 1 "never arrived", crashed again (review C3).
            for message_id in (trigger_message.part_message_ids or [trigger_message.message_id]):
                await self._bus.record_failure(
                    message_id=message_id,
                    agent_id=agent_id,
                    error=str(e),
                )
            # Once this message crosses the poison threshold,
            # `get_pending_messages` will filter it out forever (local_bus.py)
            # — this is the one chance to tell the owner it happened. Read from
            # part 1 (= the message's own id): every part carries the same
            # count, so summing them would trip the threshold N times early.
            failure_count = await self._bus.get_failure_count(
                trigger_message.message_id, agent_id
            )
            dropped = failure_count >= POISON_FAILURE_THRESHOLD
            if not is_team:
                await self._stamp_receipts(
                    messages, agent_id, channel_id,
                    RECEIPT_DROPPED if dropped else RECEIPT_FAILED,
                    reason=self._redact_error_for_owner(str(e)),
                    attempts=failure_count,
                )
            if dropped:
                await self._notify_permanent_failure(
                    agent_id=agent_id,
                    channel_id=channel_id,
                    error=str(e),
                )
                if not is_team:
                    await self._wake_sender_on_drop(
                        agent_id, channel_id, trigger_message,
                        error=str(e), attempts=failure_count, batch=messages,
                    )

        # One line per successful hop, grep-stable — emitted OUTSIDE the try
        # so observation code can never turn an already-delivered-and-acked
        # message into a recorded failure. hop mirrors queue_wait's -1.0
        # convention when created_at was unparseable, so aggregations can
        # drop incomplete rows on a single filter. The fourth quantity is
        # implicit: hop_s - queue_wait_s - turn_s = delivery (the ack +
        # room post / inbox write after the runtime returned) — a real
        # number, not rounding error.
        if _hop_done:
            _hop_s = (
                (time.monotonic() - _t_dispatch) + _queue_wait_s
                if _queue_wait_s >= 0.0 else -1.0
            )
            logger.info(
                "[bus-timing] agent={} channel={} team={} batch={} "
                "queue_wait_s={:.2f} oldest_wait_s={:.2f} turn_s={:.2f} "
                "hop_s={:.2f}".format(
                    agent_id, channel_id, is_team, len(messages),
                    _queue_wait_s, _oldest_wait_s, _turn_s, _hop_s,
                )
            )

    @staticmethod
    def _classify_error(error: str) -> str:
        """Coarse category used for (a) the cooldown de-dup key and (b) the
        hint text shown to the owner. Deliberately a substring match, not a
        parsed exception type — `record_failure` only ever gets a `str(e)`,
        the original exception is already gone by the time this runs.

        Runs on the RAW error (before `_redact_error_for_owner` masks
        anything) — classification only reads for keyword markers like
        "api_key" / "401", it never displays the raw string, so there is
        nothing to redact here.
        """
        return "provider_credential" if is_credential_error(error) else "generic"

    @staticmethod
    def _redact_error_for_owner(error: str) -> str:
        """Mask secret-looking substrings and cap the length before an
        error string is echoed into the owner-facing inbox notification.

        Provider SDKs routinely echo the offending credential back in the
        error body (OpenAI: "Incorrect API key provided: sk-..."), so
        `str(exception)` must never be written verbatim to a place the
        owner (and anyone with inbox access) can read. This is a coarse
        pattern mask, not a full secret scanner — good enough for the
        common `sk-...` / `key=...` / `Bearer ...` shapes, not a security
        boundary for arbitrary provider error formats.
        """
        return redact_secrets(error, MAX_NOTIFIED_ERROR_LEN)

    async def _notify_owner(
        self,
        agent_id: str,
        *,
        title: str,
        content: str,
        source_type: str,
        channel_id: str,
        message_id_prefix: str,
        cooldown_category: str,
    ) -> bool:
        """Write a SYSTEM_NOTICE to the owner's inbox, de-duplicated per
        (agent, channel, category).

        The one shared path for owner-facing system notices (permanent-failure
        and no-reply-delivered). Resolves the owner, writes via
        `InboxRepository`, and best-effort-logs on failure — never raises.

        Returns True when a row was written, False when the cooldown suppressed
        it, the owner could not be resolved, or the write failed.

        The window lives in `owner_notice_cooldowns` (OwnerNoticeCooldownRepository),
        not in this process: the in-process dict it replaced forgot every open
        window on restart (re-notifying on the first poll after each deploy),
        gave every trigger container its own answer, and was keyed on
        `agent:category` alone — so a permanent failure on one channel silenced
        the notice for an unrelated one on another for the whole window.

        The cooldown is armed ONLY after a successful inbox write — arming it
        up-front would let one transient write failure (DB blip, etc.) silently
        suppress the real notification for the rest of the cooldown window
        (the exact trap the original `_notify_permanent_failure` docstring
        recorded). An unreadable window fails OPEN (notify): a duplicate notice
        is the cheaper mistake than a permanently missing one.
        """
        from narranexus.platform.repository.owner_notice_cooldown_repository import (
            OwnerNoticeCooldownRepository,
        )
        from narranexus.platform.utils.db.db_factory import get_db_client

        try:
            cooldowns = OwnerNoticeCooldownRepository(await get_db_client())
            if await cooldowns.is_cooling(
                agent_id, channel_id, cooldown_category,
                FAILURE_NOTIFY_COOLDOWN_SECONDS,
            ):
                return False
        except Exception as e:  # noqa: BLE001 — fail open, see docstring
            logger.warning(
                f"MessageBusTrigger: cooldown read failed for {agent_id}/"
                f"{channel_id}/{cooldown_category}, notifying anyway: {e}"
            )

        try:
            owner_user_id = await self._get_agent_owner(agent_id)
            if not owner_user_id:
                logger.warning(
                    f"Cannot notify owner: agent {agent_id} has no resolvable owner"
                )
                return False

            import uuid

            from narranexus.platform.repository.inbox_repository import (
                InboxRepository,
            )
            from narranexus.platform.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )

            db = await get_db_client()
            await InboxRepository(db).create_message(
                user_id=owner_user_id,
                message_id=f"{message_id_prefix}{uuid.uuid4().hex[:16]}",
                title=title,
                content=content,
                message_type=InboxMessageType.SYSTEM_NOTICE,
                source=MessageSource(type=source_type, id=channel_id),
            )
            # Arm the cooldown only now that the write actually succeeded.
            await OwnerNoticeCooldownRepository(db).arm(
                agent_id, channel_id, cooldown_category
            )
            logger.warning(
                f"MessageBusTrigger: notified owner {owner_user_id} "
                f"({source_type}) for agent {agent_id} in channel {channel_id}"
            )
            return True
        except Exception as notify_err:  # noqa: BLE001 — notification is best-effort
            logger.warning(
                f"Failed to write {source_type} notification to inbox: {notify_err}"
            )
            return False

    async def _notify_permanent_failure(
        self,
        agent_id: str,
        channel_id: str,
        error: str,
    ) -> None:
        """Surface a permanently-dropped bus message to the owner's inbox.

        Without this, hitting `POISON_FAILURE_THRESHOLD` is a pure silent
        failure (upstream: NetMindAI-Open/NarraNexus#52) — e.g. a broken
        OpenAI provider key makes every `_invoke_runtime` call raise, and
        after 3 failures the message just vanishes from
        `get_pending_messages` forever with zero owner-facing signal.

        De-duplicated per (agent_id, channel_id, error category) via
        `_notify_owner`, so a burst of messages failing for one root cause on
        one channel writes at most one inbox row per
        `FAILURE_NOTIFY_COOLDOWN_SECONDS`.
        """
        category = self._classify_error(error)
        if category == "provider_credential":
            hint = (
                "This looks like a provider/credential problem — check "
                "the agent's LLM provider configuration (API key, base "
                "URL) in Provider settings, then retry the message."
            )
        else:
            hint = (
                "Check the agent's recent activity for details, then "
                "retry the message."
            )

        safe_error = self._redact_error_for_owner(error)
        content = (
            f"Your agent could not process a message on channel "
            f"{channel_id} after {POISON_FAILURE_THRESHOLD} attempts "
            f"and has stopped retrying it automatically.\n\n"
            f"Error: {safe_error}\n\n{hint}"
        )

        await self._notify_owner(
            agent_id,
            title=f"Message delivery failed: {agent_id}",
            content=content,
            source_type="message_bus_failure",
            channel_id=channel_id,
            message_id_prefix="busfail_",
            cooldown_category=category,
        )

    async def _team_roster(self, channel_id: str) -> List[dict]:
        """Who is in this room, and everything the prompt needs to say about it.

        Replaces a loop that fetched each member's whole `agents` row and kept
        only `agent_name`. The description was already in hand and thrown away,
        while "who should I hand this to" — the question the prompt tells the
        agent to answer by @mentioning someone — had nothing to stand on.

        Three batched reads, not a join. `LocalMessageBus._db` is the RAW
        backend, so a hand-written multi-table join here would be the most
        dialect-fragile statement in the package for no gain: a team is a few
        dozen agents at most, and `get_by_ids` is the repository's existing
        dialect-safe shape.

        Activity comes from `get_channel_activity`, which keys on the channel.
        That matters: `bus_agent_activity` is keyed `(agent_id, channel_id)`,
        so fetching per-agent alone returns whichever room sorts first — a bug
        this codebase has already shipped once, in the stall detector.
        """
        members = await self._bus.get_channel_members(channel_id)
        ids = [m.agent_id for m in members]
        if not ids:
            return []
        db = self._bus._db
        agents = {
            r["agent_id"]: r
            for r in (await db.get_by_ids("agents", "agent_id", ids) or [])
            if r
        }
        registry = {
            r["agent_id"]: r
            for r in (
                await db.get_by_ids("bus_agent_registry", "agent_id", ids) or []
            )
            if r
        }
        from narranexus.platform.message_bus import _bus_activity

        activity = {
            r["agent_id"]: r
            for r in (await _bus_activity.get_channel_activity(db, channel_id) or [])
            if r
        }
        out: List[dict] = []
        for agent_id in ids:
            row = agents.get(agent_id) or {}
            caps_raw = (registry.get(agent_id) or {}).get("capabilities")
            try:
                caps = json.loads(caps_raw) if isinstance(caps_raw, str) else (caps_raw or [])
            except (ValueError, TypeError):
                caps = []
            out.append({
                "agent_id": agent_id,
                "name": row.get("agent_name") or agent_id,
                "description": row.get("agent_description") or "",
                "capabilities": caps if isinstance(caps, list) else [],
                "activity": activity.get(agent_id),
            })
        return out

    async def _load_bulletin(self, team_id: str) -> List[Any]:
        """The team's standing rules, or [] if they cannot be read.

        Its own method so the degradation is testable rather than buried in the
        dispatch path. A read failure must NOT take the turn down: the turn is
        still perfectly answerable without the bulletin, so losing it is a
        degradation while losing the reply is an outage. The warning is what
        makes the degradation visible — silently returning [] would present an
        unreachable database as "this team has no rules".
        """
        try:
            from narranexus.platform.utils.db.db_factory import get_db_client
            from narranexus.platform.repository import TeamBulletinRepository

            return await TeamBulletinRepository(await get_db_client()).list_for_team(team_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[team-bulletin] could not load bulletin for {team_id}, "
                f"continuing without it: {e}"
            )
            return []

    @staticmethod
    def _render_bulletin(bulletin: Optional[List[Any]], member_map: Dict[str, str]) -> List[str]:
        """Render the team bulletin block, or nothing at all.

        Returns [] for an empty/absent bulletin — a stated acceptance criterion,
        not an optimisation. A header with "(none)" under it would ride along in
        every turn of every team that never touches the feature.

        Three groups, in this order:

        1. long-term rules, 2. current-task rules, 3. the auto-summary.

        Rules before summary because the summary is machine guesswork sitting
        next to instructions a human typed; rendered as another numbered rule it
        would be *obeyed* rather than read. It is labelled as automatic and
        possibly stale for the same reason — an agent needs some way to weigh
        the two apart, and there is none in the text itself.

        Agent-written entries are attributed, user-written ones are not: the
        attribution exists so the reader can tell which rules the team invented
        for itself. Everything unattributed came from the person in charge.

        That makes the numbered list an authority grammar, so a rule body
        cannot be allowed to start a row: `post_team_bulletin` keeps newlines,
        and a body line printed at column 0 would read as a new, unattributed —
        owner-written — rule. The attribution goes on the rule's FIRST line
        (before any body text an agent could imitate it with) and continuation
        lines are quoted under it (`body_lines`). The auto-summary is machine
        text built from room content, so it is quoted whole (`quoted_block`)
        and cannot open a header or a rule either.
        """
        if not bulletin:
            return []

        from narranexus.platform.schema.team_schema import (
            BULLETIN_SOURCE_AGENT,
            BULLETIN_SOURCE_SUMMARY,
            BULLETIN_TIER_CURRENT_TASK,
        )

        rules = [e for e in bulletin if e.source != BULLETIN_SOURCE_SUMMARY]
        summary = next(
            (e for e in bulletin if e.source == BULLETIN_SOURCE_SUMMARY), None
        )
        if not rules and summary is None:
            return []

        def _label(entry) -> str:
            if entry.source != BULLETIN_SOURCE_AGENT or not entry.author_id:
                return ""
            return f" (added by {inline_field(member_map.get(entry.author_id, entry.author_id))})"

        out: List[str] = []
        if rules:
            out += [
                "",
                "[Team Bulletin] — standing rules for this team. They apply to "
                "every reply you make here, including ones the conversation "
                "below says nothing about. Follow them without being reminded.",
            ]
            n = 0
            for e in [r for r in rules if r.tier != BULLETIN_TIER_CURRENT_TASK]:
                n += 1
                out.append(f"{n}.{_label(e)} {body_lines(e.content)}")
            current = [r for r in rules if r.tier == BULLETIN_TIER_CURRENT_TASK]
            if current:
                out.append("For the CURRENT TASK only:")
                for e in current:
                    n += 1
                    out.append(f"{n}.{_label(e)} {body_lines(e.content)}")

        if summary is not None and (summary.content or "").strip():
            out += [
                "",
                "[Team progress] — auto-summarised by the platform, NOT written "
                "by anyone, and it may lag behind what just happened. Treat it "
                "as background, and trust the conversation below over it where "
                "they disagree.",
                quoted_block(summary.content),
            ]
        return out

    async def _run_patrol(self, team_id: str, lead_agent_id: str, channel_id: str) -> None:
        """One patrol sweep: look at the board, chase what is stuck, or say nothing.

        This is the periodic activation the team room otherwise has no way to
        produce. Every member — the lead included — only wakes when @mentioned,
        so a flow advances only while each hop remembers to pass the baton, and
        a hop that forgets kills the chain with nobody structurally able to
        notice. The sweep is what notices.

        Silence is the NORMAL outcome. A patrol that announced "all good" every
        ten minutes would be exactly the standing noise this room's design keeps
        removing (the folded console, the lingering activity bubble). It speaks
        only when the model produced something to say, and even then only within
        the DB-backed speech cap.

        The cursor moves however this ends, crash included: a failed patrol
        still consumed its slot, and retrying it at once turns one broken team
        into a hot loop.
        """
        from narranexus.platform.message_bus.patrol import mark_patrolled
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        try:
            # The stack owns the sweep's activity row and its cancellation
            # registration. Both are entered inside `_patrol_body`, at the
            # point a turn is actually going to run — see the note there for
            # why opening the row earlier costs the roster the very link it
            # exists to provide.
            async with contextlib.AsyncExitStack() as stack:
                await self._patrol_body(db, stack, team_id, lead_agent_id, channel_id)
        except Exception as e:  # noqa: BLE001 — a bad sweep never breaks the poller
            logger.warning(f"[patrol] sweep failed for {team_id}: {e}")
        finally:
            await mark_patrolled(db, team_id)

    async def _patrol_body(
        self,
        db: Any,
        stack: contextlib.AsyncExitStack,
        team_id: str,
        lead_agent_id: str,
        channel_id: str,
    ) -> None:
        """The sweep proper, inside the caller's activity/cancellation scope."""
        from narranexus.platform.agent_runtime.cancel_watcher import get_cancel_watcher
        from narranexus.platform.agent_runtime.cancellation import CancellationToken
        from narranexus.platform.message_bus import _bus_activity
        from narranexus.platform.message_bus.patrol import (
            detect_stalled_items,
            may_patrol_speak,
            note_patrol_spoke,
        )

        # Facts first, and the platform's own: "is this stalled" is derived
        # from activity data, never from the model's read of the room
        # (iron rule #15). The lead's judgement starts after this line.
        #
        # This runs BEFORE the speech cap on purpose. Detection is not part
        # of speaking — it writes `stalled` through to the board, which is
        # what the user's panel renders and what paces the next sweep. When
        # the cap gated it too, a capped team stopped updating its board
        # entirely: items that went quiet during the capped window still
        # read as `in_progress` afterwards, so the UI under-reported and
        # the adaptive interval stayed at the slow pace precisely when
        # things were going wrong. A read plus a status write is also
        # nothing next to the LLM turn the cap actually exists to save.
        stalled = await detect_stalled_items(
            db, team_id, executor_agent_id=lead_agent_id
        )

        # The speech cap is checked BEFORE the turn, not after it.
        #
        # Checking only at post time meant a capped patrol still ran a full
        # LLM turn and threw the output away: with a stalled board the pace
        # is 180s (10 sweeps per 30-minute window) against a cap of 6, so
        # roughly four entire runs per window were burned for nothing —
        # right next to the "empty board, zero runs" cost guarantee this
        # feature advertises. The sweep still records its cursor below, so
        # a capped team does not turn into a hot candidate.
        if not await may_patrol_speak(db, team_id):
            logger.info(
                f"[patrol] speech cap reached for {team_id}; skipping the sweep"
            )
            return

        # The sweep's should_skip ran in `_dispatch_patrols`; the half-open
        # probe itself is claimed here, past the speech cap, so a capped
        # sweep never consumes the lead's single probe grant. The cursor
        # still moves (the caller's `finally`), so a refused lead is not a
        # hot candidate.
        # The claim re-reads the row (no prior=): the dispatch-time read is a
        # whole sweep cycle old by now, and patrols are rare enough that one
        # read per sweep is not the M1 hot path the message lane is.
        from narranexus.platform.agent_framework.loop.circuit_breaker import (
            release_probe,
            try_begin_probe,
        )

        cb_admission = await try_begin_probe(lead_agent_id)
        if not cb_admission.allowed:
            logger.info(
                f"[patrol] not starting sweep for {lead_agent_id} "
                f"(circuit-breaker: {cb_admission.reason})"
            )
            return
        # A won claim is settled by the runtime client from the turn's outcome
        # (probe_token below); this callback is the belt for every exit that
        # never reaches it (a throw while assembling the prompt, cancellation).
        # No-op once settled — the token CAS no longer matches.
        stack.push_async_callback(
            release_probe, lead_agent_id, cb_admission.probe_token
        )

        roster = await self._team_roster(channel_id)
        member_map = {r["agent_id"]: r.get("name") or r["agent_id"] for r in roster}
        team_owner = await self._get_agent_owner(lead_agent_id)
        history = await self._bus.get_recent_messages(
            channel_id, limit=TEAM_HISTORY_LIMIT
        )
        lead, work_items, team_row = await self._team_board(team_id)
        # The patrol sweep is a real turn whose reply lands in the room with
        # @mentions, so it is bound by the team's rules exactly as an @mentioned
        # member is. Omitting this made the Leader the one member the bulletin
        # did not reach — while the tool blurb below still told it the bulletin
        # loads every turn.
        bulletin = await self._load_bulletin(team_id)
        prompt = self._build_team_prompt(
            lead_agent_id, history, roster,
            owner_user_id=team_owner, team_id=team_id,
            trigger_messages=[],
            bulletin=bulletin,
            lead_agent_id=lead or lead_agent_id,
            work_items=work_items,
            team=team_row,
            patrol_stalled=[
                {
                    "title": i.title,
                    "assignee": member_map.get(i.assignee_id or "", i.assignee_id or ""),
                    "item_id": i.item_id,
                }
                for i in stalled
            ],
        )

        # A sweep is an ordinary run: it burns tokens, it can wedge, and the
        # owner must be able to stop it. So it gets the same two bindings the
        # message lane gives its runs, for the same reasons.
        #
        # `on_event_id` is the first moment the run HAS an id (Step 0 mints
        # it). Two things hang off that moment:
        #
        #   * the activity row's `event_id`. `start()` writes NULL there, and
        #     the roster's idle branch hands that column to the frontend as the
        #     entry point into the run's event log. Opening the row for the
        #     roster's sake and never filling this in would have cost the
        #     roster the very link it wanted.
        #   * the cancel watcher. The owner's stop lands in the backend
        #     process, not here, so it arrives through the DB and needs a token
        #     registered against the run id in order to fire.
        # The activity row opens HERE — not at the top of the sweep.
        #
        # `start()` writes `event_id: None` and resets `steps` / `started_at`,
        # and nothing writes the id back until `on_event_id` fires below. Open
        # it any earlier and every sweep that returns before running a turn —
        # the speech cap above, or a throw while assembling the prompt — blanks
        # the lead's link to its LAST REAL run's event log on the way past.
        # That is not a corner: with a stalled board the pace is 180s against a
        # cap of 6 per 30 minutes, so the tail of every window is exactly those
        # no-op sweeps.
        #
        # Opening it late costs nothing that was actually being bought. The
        # roster misses the two DB reads above, and `detect_stalled_items` no
        # longer consults this row for the sweeper at all (it takes
        # `executor_agent_id` and skips it), so whether the row is open during
        # detection cannot change a verdict. The board tools' fallback room
        # resolver is only read from inside the runtime call.
        act = await stack.enter_async_context(
            _bus_activity.turn(db, lead_agent_id, channel_id)
        )
        cancellation = CancellationToken()
        watcher = get_cancel_watcher(db)
        watched_run_id: list[str] = [""]
        # Unregister however the sweep exits — a token left behind keeps the
        # poll loop alive for a run that is already gone.
        stack.callback(lambda: watcher.unregister(watched_run_id[0]))

        async def on_event_id(run_id: str) -> None:
            watched_run_id[0] = run_id
            watcher.register(run_id, cancellation)
            await act.note_event_id(run_id)

        turn = await self._invoke_runtime(
            agent_id=lead_agent_id,
            sender_agent_id=f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            prompt=prompt,
            channel_id=channel_id,
            retrieval_anchor="team patrol",
            include_monologue=True,
            team_room=True,
            patrol=True,
            on_progress=act.on_progress,
            on_event_id=on_event_id,
            cancellation=cancellation,
            # The turn's team, for the MCP identity headers. Without it the
            # board tools this very prompt asks the lead to call cannot prove
            # which room they are in — tools must learn that from the server,
            # never from a model parameter.
            team_id=team_id,
            probe_token=cb_admission.probe_token,
        )
        text = (turn.text or "").strip()
        if not text:
            return  # nothing wrong, nothing said
        # Re-checked because the pre-turn gate opened minutes ago and a
        # concurrent sweep in another process may have used the budget.
        if not await may_patrol_speak(db, team_id):
            logger.info(f"[patrol] speech cap reached for {team_id}; staying quiet")
            return
        # Posted under the ROOM's marker, not the lead's id: that is what
        # keeps the line out of the agent-hop count, and it reads honestly
        # — this is the platform taking stock, not the lead chatting.
        #
        # These @mentions DELIBERATELY skip `team_cascade_depth`. Patrol's whole
        # job is to chase work that has stalled, and a chain that stalled is a
        # chain the cap has usually already stopped relaying — so applying the cap
        # here would silence the mechanism precisely when it is needed. What
        # bounds patrol instead is its own speech cap (`may_patrol_speak`, 6 per
        # 30 min per team), which is a budget on the platform's voice rather than
        # on a relay depth.
        #
        # Stated explicitly because `team_posting`'s docstring says the cap moved
        # so that "the loop-breaker was installed on the door the agent was told
        # not to use" — and this is a door with no counting on it. The difference
        # is who walks through: an agent relaying an @mention can loop, the
        # platform posting a status line every 180s at most cannot.
        await self._post_to_room(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            to_channel=channel_id,
            content=text,
            mentions=extract_team_mentions(text, member_map) or None,
            msg_type=PATROL_MSG_TYPE,
        )
        await note_patrol_spoke(db, team_id)

    async def _team_board(
        self, team_id: str
    ) -> tuple[str, List[dict], Optional[dict]]:
        """``(lead_agent_id, unfinished work items, team row)`` for the prompt.

        The team row comes back whole because this method already fetched it to
        read `lead_agent_id`; the name/description/intro that the prompt's team
        card needs were being discarded one line after being loaded.

        Best-effort: a board that cannot be read degrades to "no items" rather
        than failing the turn. The room conversation is the primary surface —
        losing the board section costs the lead some context, losing the turn
        costs the user their answer.
        """
        if not team_id:
            return ("", [], None)
        try:
            from narranexus.platform.repository.team_work_repository import (
                TeamWorkItemRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            team = await db.get_one("teams", {"team_id": team_id})
            lead = str((team or {}).get("lead_agent_id") or "")
            items = await TeamWorkItemRepository(db).list_active(team_id)
            return (
                lead,
                [
                    {
                        "item_id": i.item_id,
                        "title": i.title,
                        "assignee_id": i.assignee_id or "",
                        "status": i.status,
                    }
                    for i in items
                ],
                team,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[work-board] could not load board for {team_id}: {e}")
            return ("", [], None)

    @staticmethod
    def _member_status(row: Optional[dict]) -> str:
        """How a teammate's activity reads in the roster, or "" for silence.

        Only two states are worth a word. `running` with a fresh heartbeat says
        "busy, do not pile on"; `running` with a dead one says "this one needs
        looking at" — the single most useful thing a Leader can be told. `idle`
        is the resting state, and hanging it off every name is exactly the
        standing noise this room keeps stripping out.

        Duration comes from `started_at` (when this turn began), never
        `updated_at` (the heartbeat, which is always ~now). Integers only: the
        heartbeat ticks every 30s, so a decimal would be invented precision.

        `phase` is deliberately absent. It carries implementation step names
        like `tool:Read`; putting it in front of a model invites commentary on
        a teammate's tool use, and it churns every few seconds.
        """
        if not row:
            return ""
        if is_stalled(row):
            return "running but no signal"
        if not is_live(row):
            return ""
        secs = elapsed_seconds(row)
        if secs is None:
            return "running"
        if secs < 60:
            return f"running ({secs}s)"
        if secs < 3600:
            return f"running ({secs // 60}m)"
        return f"running ({secs // 3600}h{(secs % 3600) // 60}m)"

    @classmethod
    def _roster_lines(
        cls,
        agent_id: str,
        roster: List[dict],
        lead_agent_id: str,
    ) -> List[str]:
        """One line per member, in the same shape as the Known Agents list.

        The shape is not cosmetic. That list renders ``- `id` — "name": "desc"``
        and it is where an agent learns the identifiers `message_agent` expects.
        A roster that gave display names only forced the model to guess a
        mapping between two surfaces, so the two now read alike — through the
        same encoder (`inline_field`). A member's name and description are
        written by an agent or its owner, and a capability token can be a
        marketplace skill id from a publisher's manifest; printed raw, a
        newline or a ``: `` /
        `` · `` inside one could forge another member row, a Leader marker or a
        status. Encoded, they are JSON string literals that cannot leave their
        field (each capability token is its own literal). The id is a handle: never cut, never quoted. The description is
        capped at ``INLINE_DESCRIPTION_MAX_CHARS``, the same cap Known Agents
        uses, so one member reads the same on both surfaces.

        A quoted name is not what the @mention parser reads (it takes a bare
        ``@word``), so each row also shows the member's exact mention token
        (`mention_token`, checked against the parser itself), or says the name
        has none and names what does reach that member (@all, or message_agent
        with the row's id). Without it an agent copying the name writes ``@"Ana"`` and
        the mention resolves to nobody, silently.

        The agent's OWN row is included and marked. Leaving yourself off the
        list of who is present is the confusion this card exists to end, not a
        tidy-up — and the lead marker is on every row, so a non-lead can finally
        see who is supposed to be driving.

        An unset description prints NOTHING rather than its placeholder: the
        Known Agents list learned in 2026-08-04 that repeating "a new agent
        ready for configuration" beside every peer reads as "none of these are
        usable".
        """
        from narranexus.platform.schema.entity_schema import is_agent_description_unset

        if not roster:
            # NOT "just you": this agent is itself a member, so an empty roster
            # means the read came back empty, not that the room is deserted.
            # Same rule as the team card — say nothing rather than assert
            # something the data does not support.
            return []
        member_map = {
            str(r.get("agent_id", "")): r.get("name") or str(r.get("agent_id", ""))
            for r in roster
        }
        out = [
            f"Channel members RIGHT NOW (besides the user), {len(roster)}. To "
            f"@mention one, write the @token shown on their row exactly — "
            f"never the quoted name:"
        ]
        for r in roster:
            rid = r.get("agent_id", "")
            line = f"- `{inline_field(rid, None)}` — {inline_field(r.get('name') or rid)}"
            token = mention_token(str(rid), member_map)
            # No token wakes this member alone, so say what does reach them
            # instead of leaving a dead end: @all wakes the room, and
            # message_agent takes the id printed at the start of this row.
            line += (
                f" @{token}" if token
                else " (no @mention token — @all reaches everyone; message_agent "
                "with the id above reaches them alone)"
            )
            if rid == agent_id:
                line += " (you)"
            if rid and rid == lead_agent_id:
                line += " · Leader"
            desc = r.get("description") or ""
            if not is_agent_description_unset(desc):
                # Marked when cut (by the encoder) and cut at the Known Agents
                # cap: one member described two ways on two surfaces is how a
                # reader learns to distrust both.
                line += f": {inline_field(desc, INLINE_DESCRIPTION_MAX_CHARS)}"
            # Capability tokens include marketplace skill ids, which come from a
            # publisher's manifest unchecked: labels, so a token cannot start a
            # row or forge a ` · Leader` / status field on this one.
            all_caps = [str(c) for c in (r.get("capabilities") or [])]
            caps = [inline_field(c) for c in all_caps[:6]]
            if caps:
                more = f" +{len(all_caps) - len(caps)} more" if len(all_caps) > len(caps) else ""
                line += f" · can: {', '.join(caps)}{more}"
            # Own status is not news to oneself.
            if rid != agent_id:
                status = cls._member_status(r.get("activity"))
                if status:
                    line += f" · {status}"
            out.append(line)
        return out

    @staticmethod
    def _team_card_lines(team: Optional[dict]) -> List[str]:
        """The team's identity block: name, purpose, house rules.

        Every field is optional and an absent one renders as NOTHING — never as
        an empty heading. A blank "Why this team exists:" reads as "this team
        has no purpose", which is worse than not raising the question.

        `intro_md` is capped at ``TEAM_INTRO_MAX_CHARS`` and the cut is always
        announced. The column is MEDIUMTEXT behind a free-text box, so it can be
        arbitrarily long, and an unbounded field in a per-turn prompt crowds out
        the scrollback and roster — the parts that decide what happens THIS
        turn. The cut lands on a line boundary where one is near, so a markdown
        table or fence is not sliced through the middle.
        """
        if not team:
            return []
        lines: List[str] = []
        name = str(team.get("name") or "").strip()
        if name:
            lines += ["", f"[Team] {inline_field(name)}"]
        description = str(team.get("description") or "").strip()
        if description:
            lines.append(f"Why this team exists: {description}")
        intro = str(team.get("intro_md") or "").strip()
        if intro:
            if len(intro) > TEAM_INTRO_MAX_CHARS:
                cut = intro[:TEAM_INTRO_MAX_CHARS]
                nl = cut.rfind("\n")
                if nl > TEAM_INTRO_MAX_CHARS // 2:
                    cut = cut[:nl]
                intro = (
                    f"{cut.rstrip()}\n…(intro truncated at "
                    f"{TEAM_INTRO_MAX_CHARS} chars — the full version lives in "
                    f"the team settings)"
                )
            lines += ["How this team works:", intro]
        return lines

    def _build_team_prompt(
        self,
        agent_id: str,
        history: List[BusMessage],
        roster: List[dict],
        owner_user_id: Optional[str] = "",
        team_id: str = "",
        trigger_messages: Optional[List[BusMessage]] = None,
        lead_agent_id: str = "",
        work_items: Optional[List[dict]] = None,
        patrol_stalled: Optional[List[dict]] = None,
        team: Optional[dict] = None,
        *,
        bulletin: Optional[List[Any]],
    ) -> str:
        """Group-chat prompt for a team room. The agent's plain reply is posted
        back into the shared room (the user + teammates see it), so — unlike the
        peer/owner-relay path — there is no notify_owner step.

        ``history`` is the recent room scrollback (oldest→newest) so the agent
        sees files/images posted by ANYONE, not only the message that @mentioned
        it; ``trigger_messages`` are the @mentions for this agent — what it
        should respond to."""
        from narranexus.platform.message_bus._bus_attachment_impl import build_bus_markers
        from narranexus.platform.agent_runtime.steer_channel import STEER_PROVENANCE_RULE

        member_map = {r["agent_id"]: r.get("name") or r["agent_id"] for r in roster}
        me = member_map.get(agent_id, agent_id)
        lines = [
            "[Team Group Chat]",
            f"You are {inline_field(me)} in a team group chat with the user and "
            f"your teammates.",
        ]
        lines += self._roster_lines(agent_id, roster, lead_agent_id)
        lines += [
            "These are the ONLY participants who can see this chat. Someone "
            "named in the history but not in that list has LEFT or was never "
            "here — they are not present.",
            # Kills the "I forwarded it ✅" white lie: everyone already sees room
            # files, so there is nothing to forward — @mention is enough.
            "Every member already sees every message and file posted in THIS "
            "room (they are in the conversation below). So NEVER 'forward' or "
            "'send' a file that's already here, and never claim you did — to "
            "bring a teammate in, just @mention them and they'll see it too.",
            # A team run is steerable: a message that arrives WHILE this turn
            # runs is appended live (render_injection), tagged by source. The
            # tag is authority-bearing (owner vs teammate), so the model must be
            # told how to read it or a teammate could forge an owner tag — the
            # nonce boundary only holds once the prompt states this rule. Shared
            # verbatim with the chat / IM producers via the one constant.
            STEER_PROVENANCE_RULE,
        ]
        # --- The team card ---------------------------------------------
        #
        # Ahead of the working instructions on purpose: "where am I, with whom,
        # and why" is the frame everything below is read through. The owner
        # writes `description` / `intro_md` in the management UI believing they
        # set the team's terms; until this section existed neither field had a
        # single consumer on the agent side, so the answer to "why are we all
        # here" was never in the room and the owner had no way to find that out.
        lines += self._team_card_lines(team)

        if owner_user_id and team_id:
            from narranexus.platform.utils.workspace_paths import team_shared_dir
            shared = team_shared_dir(owner_user_id, team_id)
            lines.append(
                f"Team shared folder: {shared} — files placed here (via "
                f"team_share_file) are visible to every teammate; open them "
                f"with the Read tool. To find out what is already in there, "
                f"call team_list_files(team_id=\"{team_id}\") rather than "
                f"guessing a path or asking someone to repeat one. "
                f"WRITE ANYTHING THE TEAM SHOULD SEE HERE TOO — reports, "
                f"pages, data files you intend to register as artifacts. Your "
                f"own workspace is private to you, so work left there is work "
                f"your teammates cannot open or continue."
            )

        # Two standing blocks now precede the conversation, in this order:
        # the bulletin (rules that govern HOW you reply), then the work
        # board (WHAT is outstanding) and the Leader's duties. Rules first
        # because they frame everything after them, including the board.
        # A tool nobody is told about is a tool nobody uses. Kept to one line
        # and worded to discourage over-pinning: the bulletin's budget is small
        # and shared with the user's own rules, so an agent that treats it as a
        # notepad crowds out the very rules it is supposed to obey.
        lines += [
            "",
            "If the team settles on a convention that should govern FUTURE "
            "replies (an output format, where files go), pin it with "
            "team_pin_rule so nobody has to repeat it — every teammate "
            "loads it every turn. Findings, status and conversation belong in "
            "the chat, not the bulletin.",
        ]

        # Before the conversation on purpose: these are the standing constraints
        # the messages are to be read UNDER. Appended after twenty lines of chat
        # they would read as a footnote to the chat instead of a frame for it.
        lines += self._render_bulletin(bulletin, member_map)
        # --- The work board, and (for the lead) what it obliges -------------
        #
        # Injected rather than fetched: if seeing your own board required
        # calling a tool, "what did I hand out" would depend on the model
        # choosing to look — the same dependency iron rule #15 keeps off
        # correctness-critical paths. Every member sees the board (it is how
        # they know what they own); only the lead is given the duty to drive it.
        lines += ["", "[Work board] — tasks that outlive this turn:"]
        if work_items:
            # Capped since the board stopped being hand-written. Every row used
            # to cost a Leader a deliberate tool call; errands now open
            # themselves from an @mention, so the length of this section became
            # a function of how chatty the room is — and it is rendered into
            # EVERY member's prompt on EVERY turn.
            #
            # Oldest-first is the order `list_active` already returns, and it is
            # the right one to keep: the rows most likely to be stuck are the
            # ones that have been open longest, and they are what this section
            # exists to surface.
            shown = work_items[:TEAM_BOARD_MAX_ITEMS]
            for item in shown:
                # Title and assignee name are author-written labels, encoded so
                # a title cannot forge a second row with a fake `id=`; the id
                # is a handle `team_work_complete` takes verbatim.
                who = member_map.get(item.get("assignee_id") or "", "")
                who = inline_field(who) if who else "unclaimed"
                lines.append(
                    f"- [{item.get('status')}] {inline_field(item.get('title'))} "
                    f"({who}) · id={inline_field(item.get('item_id'), None)}"
                )
            hidden = len(work_items) - len(shown)
            if hidden > 0:
                # Stated, not silent. A truncated board that reads as complete
                # would have the lead conclude the rest was already closed —
                # the same rule iron rule #16 states for a user-visible stream,
                # applied to the model's view of its own team.
                lines.append(
                    f"- (+{hidden} more not shown — use the work board tools "
                    f"to see the rest)"
                )
        else:
            lines.append("- (no open work items)")

        if patrol_stalled is not None:
            # A patrol sweep, not a reply to anyone. Nobody addressed this
            # agent — the platform woke it because work is outstanding.
            lines += [
                "",
                "[Patrol] Nobody messaged you. The platform woke you because "
                "this team has unfinished work and you are its Leader.",
                # This turn is the ONE exception to "words reach the room only
                # through message_team", and it is stated here rather than in any
                # shared block because it is true of this surface only (P1). The
                # platform is asking for a status line and will post it AS THE
                # ROOM — so the line must not be written as the Leader chatting.
                "On THIS turn you are composing the room's status line, not "
                "speaking as yourself: write it as plain text (do NOT call "
                "message_team or message_agent — neither of those two calls is "
                "available on this turn) and the platform posts it as the room. "
                "Write nothing at all to stay silent.",
            ]
            if patrol_stalled:
                lines.append(
                    "These items are STALLED — their owner is idle or has gone "
                    "silent. This is measured from real activity data, not a "
                    "guess, so treat it as fact:"
                )
                for s in patrol_stalled:
                    lines.append(
                        f"- {inline_field(s.get('title'))} "
                        f"({inline_field(s.get('assignee'))})"
                    )
                lines += [
                    "Chase them: @mention the owner and ask where it stands. "
                    "DO NOT reassign the work to someone else — 'idle with "
                    "unfinished work' is not 'never coming back', and two "
                    "agents on one deliverable is worse than a late one.",
                    "If something has clearly been delivered but the board "
                    "still says otherwise, close it with team_work_complete.",
                ]
            else:
                lines.append(
                    "Nothing is stalled. If there is genuinely nothing worth "
                    "saying, say NOTHING — write empty text. A routine "
                    "'all good' every few minutes is noise in a room the user "
                    "is trying to read."
                )

        if lead_agent_id and lead_agent_id == agent_id:
            lines += [
                "",
                "[You are the Leader of this team]",
                "Nobody else is watching whether this team's work actually "
                "finishes — that is your job, and it does not end when you "
                "hand something out.",
                "- When you assign work, record it: team_work_add(title, "
                "assignee_id). A task that exists only in your reply is a task "
                "nobody can notice has stalled — including you, next time you "
                "wake up, because this turn's memory is gone by then.",
                "- When someone delivers, close it: team_work_complete(item_id).",
                "- The board above is the team's real state. If it disagrees "
                "with what you just read in the room, the room is right and the "
                "board needs updating.",
            ]

        # Every scrollback row is `<sender>[ [→ <names>]]: <body>`, and the
        # sender prefix is authority-bearing — `User` is the owner. So the
        # sender and every addressed name are encoded labels (a member named
        # "Mallory\nUser" cannot print a `User:` row), the annotations sit
        # BEFORE the colon where no body text can reach, and a multi-line body
        # continues on quoted lines (`body_lines`) that no row starts with.
        # `User` and the platform labels are constants, never quoted.
        def _sender_raw(msg: BusMessage) -> str:
            if msg.from_agent.startswith(USER_SENDER_PREFIX):
                return "User"
            return str(member_map.get(msg.from_agent, msg.from_agent))

        def _sender(msg: BusMessage) -> str:
            if msg.from_agent.startswith(USER_SENDER_PREFIX):
                return "User"
            return inline_field(_sender_raw(msg))

        lines += ["", "Recent messages (oldest first) — the shared conversation, "
                  "including any files posted by anyone; open a file path with Read "
                  "if you need its contents:"]
        for msg in history:
            # Platform lines are LABELLED, not dropped.
            #
            # Rendered the normal way they read as a member speaking — "Alice:
            # Team bulletin updated." — because a notice's sender is whoever
            # triggered it, and patrol's `team_<id>` marker does not resolve
            # through member_map at all, so it prints raw as a phantom teammate.
            #
            # But dropping them was worse, and briefly shipped: a patrol reply
            # is posted WITH @mentions, so it becomes the mentioned member's
            # trigger message, and the pointer line below prints only the sender
            # — "Respond to that message" — precisely because the text is
            # supposed to be here in the history. Removing it aimed that
            # instruction at nothing. The one sender this filter exists to
            # silence was the only one it broke.
            if (msg.msg_type or "") in PLATFORM_MSG_TYPES:
                # The label is shared with the module's unread list, which
                # renders the SAME rows; only the shape differs (prefix here,
                # sender field there). See SYSTEM_SENDER_LABEL.
                lines.append(f"{SYSTEM_SENDER_LABEL} {body_lines(msg.content)}")
                continue
            sender = _sender(msg)
            # Who the line was AIMED at. `mentions` has always been on the
            # message and this prompt never read it, so a room coordinating
            # three people arrived as undifferentiated chatter and the agent had
            # to infer which lines concerned it. Knowing a request already has
            # an owner is also what stops two agents doing the same job.
            addressed = ""
            targets = [m for m in (msg.mentions or []) if m]
            if targets:
                named = [
                    "you" if m == agent_id else inline_field(member_map.get(m, m))
                    for m in targets
                ]
                addressed = f" [→ {', '.join(named)}]"
            lines.append(f"{sender}{addressed}: {body_lines(msg.content)}")
            # The marker encodes its own sender field, so it takes the raw name.
            marker = build_bus_markers(msg.attachments, from_agent=_sender_raw(msg))
            if marker:
                lines.append(marker)

        # Point the agent at what it must answer — the latest message that
        # @mentioned it (it's already in the history above, shown in order).
        if trigger_messages:
            # A patrol chase reaches here as a real trigger, and its sender is
            # the synthetic `team_<id>` marker, which member_map cannot resolve.
            # Naming it verbatim invents a teammate the agent may then try to
            # @mention back.
            def _who(m: BusMessage) -> str:
                if (m.msg_type or "") in PLATFORM_MSG_TYPES:
                    return _platform_trigger_label(m.msg_type or "")
                return _sender(m)

            tail = (
                "If it refers to a file/image shown above, open the path with "
                "the Read tool first, then reply."
            )
            # The honesty check is per BATCH, not "is there exactly one". Two
            # messages inside one poll window is ordinary, and if the user named
            # nobody in either then BOTH carry a synthesised mention — announcing
            # "2 messages @mentioned you" would be the same invented attention
            # this branch exists to remove, one branch over.
            routed = [m for m in trigger_messages if m.routed_by == "default_responder"]
            all_routed = len(routed) == len(trigger_messages)
            if len(trigger_messages) == 1:
                tm = trigger_messages[0]
                if all_routed:
                    lines += [
                        "",
                        f"{_who(tm)} posted this without @mentioning anyone. "
                        f"You are this team's default responder, so it came to "
                        f"you. Answer it yourself. Hand it to a teammate only "
                        f"when the work is clearly theirs — and then hand it "
                        f"over, do not also half-answer it. {tail}",
                    ]
                else:
                    lines += [
                        "",
                        f"You were just @mentioned by {_who(tm)}. Respond to "
                        f"that message. {tail}",
                    ]
            else:
                # Every ASK, not just the last. Naming only `[-1]` left the
                # earlier asks sitting in the scrollback looking like everyone
                # else's traffic — asked, and silently dropped, which reads to
                # the user as the agent ignoring them.
                #
                # But an ask, not every line. "Address ALL of them" (until
                # 2026-09-03) made a teammate's "done, thanks" owe a reply as
                # much as a question did, and the replies to those were the
                # ping-pong the owner asked to stop. The rule is now the one
                # the module states for DMs: a question or a request gets an
                # answer; an FYI or an acknowledgement gets nothing.
                if all_routed:
                    head = (
                        f"{len(trigger_messages)} messages arrived with no "
                        f"@mention. You are this team's default responder, so "
                        f"they came to you. Reply to the ones that ask you for "
                        f"something; one that only informs you needs no reply:"
                    )
                else:
                    head = (
                        f"{len(trigger_messages)} messages @mentioned you since "
                        f"your last turn. Reply to the ones that ask you for "
                        f"something — a real question left unanswered reads as "
                        f"ignoring it. One that only informs you, or confirms "
                        f"what you said, needs no reply:"
                    )
                lines += ["", head]
                for tm in trigger_messages:
                    # In a mixed batch neither label is true of the whole, so
                    # each line says which it is.
                    mark = (
                        " [no @mention — routed to you]"
                        if (routed and not all_routed
                            and tm.routed_by == "default_responder")
                        else ""
                    )
                    # Same row shape as the scrollback: the mark before the
                    # colon, the body's later lines quoted under the row.
                    lines.append(f"- {_who(tm)}{mark}: {body_lines(tm.content)}")
                lines.append(tail)
        # Delivery mechanism — how words get INTO the room. TRUE ONLY when the
        # reply is a `message_team` call, i.e. NOT on a patrol turn: there the
        # platform posts the plain-text status line AS the room (the patrol block
        # above says so), so "nothing outside the call reaches the room" is false
        # and there is no call to make. Gating this is what stops the prompt from
        # ordering `message_team` a hundred lines after forbidding it on patrol.
        if patrol_stalled is None:
            lines += [
                "",
                f"Speak in this room by calling message_team(team_id=\"{team_id}\", "
                "text=...). Rules:",
                "- Put ONLY the message in `text` — natural, conversational text "
                "(markdown is fine). It goes to the group as-is; everyone sees it.",
                # The room is a tool call now (2026-08-17). It used to be the
                # agent's plain text, auto-posted by the trigger — which made
                # this the one surface where "plain text reaches nobody" was
                # false, and every layer that states the general rule
                # contradicted this one. What replaced that ban is a positive
                # instruction: nothing is forbidden here, there is simply one
                # verb that puts words in the room.
                "- Nothing you write outside that call reaches the room. Thinking "
                "it through in plain text first is fine and private — but the "
                "turn only speaks when you make the call.",
                "- If you have nothing to add, make no call at all. Silence is a "
                "legitimate answer here and leaves no mark in the room; a "
                "routine acknowledgement is not.",
                "- You MAY use action tools alongside it: the built-in Read tool "
                "to open a file path shown above, and team_share_file to publish "
                "a file YOU produced to the team folder (then mention the returned "
                "path in your message). Do the action, then say what you found.",
            ]
        # Writing + mention rules that hold WHATEVER the delivery surface —
        # a `message_team` reply or a patrol status line. Unconditional, so
        # patrol keeps the no-narration / mention / no-promise disciplines it
        # also needs (it @mentions stalled owners, it must not promise). Its OWN
        # blank line + header: the separator is NOT borrowed from the gated block
        # above (which patrol does not get), or on patrol these bullets orphan
        # onto the message history. The header is surface-neutral — "write" holds
        # for both a message and a status line; no delivery-specific word.
        lines += [
            "",
            "When you write for this room:",
            "- Do NOT narrate your process or thinking in the message. No "
            "\"Let me…\", no \"I need to find…\", no tool/function names, no "
            "step-by-step. Just talk.",
            "- Keep it short, like a real group chat. To pull in a teammate, "
            "@mention them by name (e.g. @Name); say @all for everyone — but only "
            "when you genuinely need them, not as a reflex.",
            # 2026-09-03. The owner read a 23-message exchange between three
            # members that followed one 19-character question: each reply
            # opened with 收到, restated the correction the previous reply had
            # already accepted, and @mentioned both other members "to keep
            # them in the loop" — which woke them, which produced the next
            # one. Every rule below names one of those three moves.
            "- Say only what is NEW: your result, your finding, your decision, "
            "or one real question. Do not open with an acknowledgement (收到 / "
            "got it), do not restate what a teammate already said or accepted, "
            "and do not post a wrap-up of the thread unless the user asked for "
            "one.",
            "- Reply to whoever asked you, and only to them. Do not @mention "
            "the rest of the room to keep them informed — they read the room, "
            "and every @mention starts a turn.",
            "- You may ONLY @mention a current channel member listed above. Do "
            "NOT @mention anyone who is not in that list — they are not in the "
            "channel and cannot see or answer it. If you want someone else "
            "involved, ask the user to add them instead of @mentioning them.",
            # The Dunhuang rule (2026-06-30). A six-stage pipeline died because
            # one agent answered "收到，开始处理……完成后交付 @A4": the promise
            # WAS the run's whole output, so the run ended `completed` and
            # nothing existed to produce the "完成后".
            #
            # Phrased with the alternatives spelled out, not as a bare ban. The
            # 0802 WeChat report is the other failure mode on this same axis —
            # a protocol that only says "don't" makes silence the compliant
            # answer, and silence is what this room can least afford.
            #
            # Reduces how often the platform's guard is consulted; it is NOT
            # the guard (iron rule #15). `message_bus/errand.py` keeps a
            # hand-off from the USER or the LEAD on the board whether or not
            # the model obeys this line. Since 2026-09-03 a member-to-member
            # hand-on books nothing (`opens_handoffs`), so for that shape the
            # line below is the only discipline — accepted, see errand.py.
            "- **Do not promise future delivery.** Nothing of yours keeps "
            "running once this turn ends, so \"完成后交给你\" / \"I'll report "
            "back when it's done\" is a promise nothing will keep. Instead: "
            "finish the work in THIS turn and reply with the result, or say "
            "plainly how far you got and what you need, or schedule the "
            "follow-up explicitly with `job_create` if you have it.",
        ]
        return "\n".join(lines)

    async def _incoming_is_reply_to_my_errand(
        self,
        agent_id: str,
        channel_id: str,
        incoming: List[BusMessage],
    ) -> bool:
        """Is this batch an ANSWER to an errand of ours, or a QUESTION to us?

        Decided from a fact recorded on the message itself
        (``bus_messages.sender_turn_source``): the sender writes WHICH KIND of
        turn produced it — an owner-facing turn ("chat"/"job"/…) or an
        errand-continuation bus turn (``BUS_ERRAND_TURN_SOURCE``) means it is
        running an errand and asking us; plain "message_bus" means it was in
        a peer-ANSWERING turn.

        Plain "message_bus" alone is NOT sufficient for Owner Relay, because
        the stamp records the sender's turn kind, not this message's intent:
        an agent in a peer-answering turn can still ASK (fan out to a third
        agent to compose its answer). So the batch only reads as a reply if
        WE actually hold an errand in this channel — at least one prior send
        of ours stamped with a non-"message_bus" source (or a pre-stamp
        legacy NULL, which gets the old benefit of the doubt). An agent that
        never asked anything here cannot be owed an answer.

        The errand stamp is per-SEND, not per-turn, and that is load-bearing
        here: a bus turn also answers unrelated peers whose unread the platform
        injected from other channels, so only sends aimed at the sender's own
        errand scope carry it (``_send_turn_source`` in the bus module's MCP
        tools). Stamping the whole turn broke exactly this method's contract
        from the other side — an ANSWER to an unrelated peer arrived stamped as
        a question, so that peer stopped relaying to its own owner
        (2026-08-03 review).

        Residual holes, accepted and documented — do not read this list as
        "risk exhausted", read it as what is known:

        1. Stale errand: if we once ran an errand in this channel and a peer
           LATER asks us a fresh question from a peer-ANSWERING turn (a case
           no prompt of ours guides), the old errand rows still vote Owner
           Relay. Per-message intent would need the sender to declare
           ask-vs-answer per send; a turn-kind stamp cannot express it. Equals
           the pre-fix behaviour.
        2. Mutual live errands in one DM channel: DM channels are reused
           symmetrically, so if the errand peer ALSO asked us something and we
           answer them inside our errand-continuation turn, that answer is
           aimed at the errand scope and carries the errand stamp — they then
           answer the peer instead of relaying to their own owner. Needs both
           owners to have errands in flight at the same time toward each
           other. This is the direction we chose: the alternative (stamping
           the whole turn) broke the case the platform ITSELF guides — unread
           from other channels is injected every turn and the prompt requires
           answering it — so it fired far more often.
        3. Group errand channel: the errand scope matches by channel as well
           as by peer, so a send into a GROUP channel that is the errand
           channel stamps every member's copy as a question. Bus errands run
           over auto-created DM channels, so this needs a hand-built group
           channel used as an errand channel.
        4. Uppercase / hand-written stamps: the errand-row check compares the
           stored value exactly (SQL ``<>``), so a row written outside our
           writers with a different casing counts as an errand row → Owner
           Relay. Same direction as every other degradation here.

        Closing 1 and 2 for good needs per-message intent — the sender saying
        "this one is a question" on each send — which the review floated and
        which we did not take, because it puts a correctness-critical bit back
        on model obedience (iron rule #15: a machine-knowable fact must not
        depend on which model the user picked). Revisit only with a default
        that is derived, never assumed.

        Why not infer it from channel ordering (the first attempt, twice):
        "have I spoken here" flips as soon as we answer once, so a follow-up
        question re-broke the bug; "who opened the channel" is fixed forever,
        because ``send_to_agent`` finds a DM channel SYMMETRICALLY and REUSES
        it — so once A has DM'd B, every errand B later runs toward A is
        misclassified for BOTH sides, permanently (P1 2026-08-03 review). The
        fact is per-message, so it has to be stored per-message.

        Degradation, in order: unknown source but WE have never spoken here →
        we are plainly the asked party; otherwise → Owner Relay, the
        pre-2026-08-01 behaviour (a wrongly-relayed answer is cosmetic, while
        wrongly suppressing Owner Relay resurrects the silent failure that
        directive exists to prevent).
        """
        sources = {
            (getattr(m, "sender_turn_source", None) or "").strip().lower()
            for m in incoming
        }
        sources.discard("")
        if sources:
            if sources != {"message_bus"}:
                # Any owner-facing or errand-continuation send in the batch
                # means we are being asked.
                return False
            # Every incoming message came from a peer-answering turn. Reply
            # to OUR errand only if we actually have one here.
            return await self._i_have_errand_in_channel(agent_id, channel_id)

        # No recorded source (legacy rows, or an adapter that dropped the
        # header). Fall back to "have we ever spoken here": absence is still
        # unambiguous — a channel we have never sent into cannot hold a reply
        # to an errand of ours.
        try:
            incoming_ids = {
                m.message_id for m in incoming if getattr(m, "message_id", None)
            }
            ph = self._bus._db.placeholder
            rows = await self._bus._db.execute(
                # No human-sender filter here on purpose: from_agent is bound
                # to agent_id, so a usr_-prefixed sender cannot match anyway.
                # (team_posting.team_cascade_depth needs one because it reads EVERY message
                # in the channel.) A dead predicate would be worse than none —
                # 'usr_%' is not even precise, since _ is a LIKE wildcard.
                f"SELECT message_id FROM bus_messages WHERE channel_id = {ph} "
                f"AND from_agent = {ph}",
                (channel_id, agent_id),
            )
            for r in rows or []:
                if str(r["message_id"]) not in incoming_ids:
                    return True
            return False
        except Exception as e:  # noqa: BLE001 — prompt shaping, never flow control
            logger.debug(
                f"MessageBusTrigger: could not classify channel {channel_id} "
                f"({e}); assuming owner-relay"
            )
            return True

    async def _i_have_errand_in_channel(
        self, agent_id: str, channel_id: str
    ) -> bool:
        """Did WE ever ask something in this channel on an errand?

        True when at least one of our own sends here was stamped with a
        non-"message_bus" turn source (owner-facing turn, or the
        errand-continuation stamp) — or carries a legacy NULL stamp, which
        predates ``sender_turn_source`` and keeps the pre-fix benefit of the
        doubt. False when we never spoke here, or every send of ours was a
        peer-answering turn: then an incoming "message_bus"-stamped batch
        cannot be a reply owed to us, it is a fresh question.

        DB error → True (Owner Relay), same degradation direction as the
        caller: the silent-failure mode this directive exists to prevent is
        worse than a cosmetic extra relay.
        """
        try:
            ph = self._bus._db.placeholder
            # Existence check, pushed down: this runs on the most common bus
            # trigger path ("a peer answered me"), and DM channels are found
            # symmetrically and REUSED forever — so a client-side scan would
            # grow without bound over the life of an agent pair. IS NULL / <>
            # / LIMIT 1 mean the same thing on SQLite and MySQL.
            rows = await self._bus._db.execute(
                f"SELECT 1 FROM bus_messages "
                f"WHERE channel_id = {ph} AND from_agent = {ph} "
                f"AND (sender_turn_source IS NULL OR sender_turn_source <> {ph}) "
                f"LIMIT 1",
                (channel_id, agent_id, WorkingSource.MESSAGE_BUS.value),
            )
            return bool(rows)
        except Exception as e:  # noqa: BLE001 — prompt shaping, never flow control
            logger.debug(
                f"MessageBusTrigger: errand check failed for channel "
                f"{channel_id} ({e}); assuming owner-relay"
            )
            return True

    def _build_prompt(
        self,
        messages: List[BusMessage],
        owner_user_id: Optional[str] = "",
        owner_name: str = "",
        *,
        i_started_this_exchange: bool,
    ) -> str:
        # NOTE: this builds the full EXECUTION prompt (peer messages + the
        # repeated Owner-Relay boilerplate). For narrative retrieval, embed
        # build_bus_anchor(messages) instead — see the 2026-06-01 design doc.
        """
        Build a prompt from a list of pending messages.

        Includes all messages in the batch so the agent has full context.

        If `owner_user_id` is known, appends an owner-relay directive telling
        the agent it MUST call notify_owner(user_id=<owner>,
        ...) to surface the peer exchange back into the owner's chat. Without
        this directive, agents treat peer exchanges as self-contained (they
        reply to the peer or stay silent), and the original owner who asked
        "go talk to agent B for me" never hears back — the reply only lands
        in the Inbox. observed as a silent-failure UX issue in production.

        ``i_started_this_exchange`` decides WHICH directive applies, and it
        matters (P1 2026-08-03, verified live 3/3): Owner Relay was appended
        on every DM turn, so an agent receiving a FRESH question from a peer
        was told "your owner originally asked you to contact this peer agent,
        they are waiting in chat" — false, and it made the recipient answer
        the OWNER and treat the errand as discharged. The asking agent
        (which had promised its user a report) was left waiting forever.
        The models were obeying us; the prompt was lying to them.

        - True  → this batch is the REPLY to an errand of ours: relay it to
                  our owner.
        - False → we are the one being ASKED: answer the PEER on the bus.
                  Our owner asked for nothing and is not waiting.

        Decided by ``_incoming_is_reply_to_my_errand`` from a fact recorded
        on the message itself (``sender_turn_source``) — NOT from "have I
        spoken in this channel", which is only the degradation path and is
        wrong for follow-ups (see that method for why, and for the residual
        holes this pair of directives still has).

        Note the directive an OWNER-RELAY turn receives (item 3: "send a
        clarifying question with message_agent") produces a peer send from
        a bus turn, which is why the same verdict also travels to the tools as
        this turn's errand scope — see ``_invoke_runtime``.
        """
        from narranexus.platform.message_bus._bus_attachment_impl import build_bus_markers

        lines = ["[Message Bus - Incoming Messages]", ""]
        for msg in messages:
            # The body is quoted whole: its lines can never read as another
            # message's `From:` / `Time:` header.
            block = (
                f"From: {msg.from_agent}\n"
                f"Time: {msg.created_at}\n"
                f"{quoted_block(msg.content)}\n"
            )
            marker = build_bus_markers(msg.attachments, from_agent=msg.from_agent)
            if marker:
                block += f"{marker}\n"
            lines.append(block)

        if owner_user_id and not i_started_this_exchange:
            # Inbound question: the peer is waiting, not our owner.
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Answer the peer — REQUIRED")
            lines.append("")
            lines.append(
                "A peer agent is asking YOU something. You did not start this "
                "exchange and your owner has not asked you for anything, so "
                "your owner is NOT waiting in chat for this."
            )
            lines.append("")
            lines.append(
                "**The peer cannot see anything you tell your owner.** The only "
                "channel that reaches the agent who asked is a bus reply. If the "
                "peer is relaying a question on ITS owner's behalf, that human is "
                "waiting at the other end of the peer — answering your own owner "
                "instead leaves them waiting forever."
            )
            lines.append("")
            lines.append("**What to do this turn:**")
            lines.append(
                "1. If you can answer → reply to the asker with "
                "`message_agent(to=<the sender above>, "
                "text=<your answer>)`. That is usually the point of the turn. "
                "If what they ask means acting elsewhere — posting in a team "
                "room you belong to, messaging someone else — you can do that "
                "this turn too; your teams and peers are listed in your context."
            )
            lines.append(
                "2. If you need something clarified before you can answer → ask "
                "the peer back via `message_agent`."
            )
            lines.append(
                "3. Only ALSO call `notify_owner` when your "
                "owner genuinely needs to know (a decision only they can make, "
                "or something affecting their work). It is never a substitute "
                "for replying to the peer."
            )
            lines.append(
                "4. Silence is only correct for a closing acknowledgment "
                "(\"thanks\", \"got it\"). A question is never a ping-pong — "
                "answer it."
            )
        elif owner_user_id:
            lines.append("")
            lines.append("---")
            lines.append("")
            lines.append("## Owner Relay — REQUIRED")
            lines.append("")
            lines.append(
                f"Your owner **{owner_name or owner_user_id}** originally asked "
                f"you to contact this peer agent. They are waiting in chat for "
                f"the answer."
            )
            lines.append("")
            lines.append(
                "The owner's chat view does NOT automatically receive the "
                "peer's reply. The ONLY channel that surfaces this exchange "
                "to the owner is `notify_owner`. If you do "
                "not call it, the owner sees nothing — they only know "
                "there's a new entry in some inbox they may not be looking "
                "at. This is a silent-failure pattern we explicitly want to "
                "avoid."
            )
            lines.append("")
            lines.append("**What to do this turn:**")
            lines.append(
                "1. Understand the peer reply above."
            )
            lines.append(
                "2. If the peer's reply answers / progresses the owner's "
                "original request → call "
                f"`notify_owner(agent_id=<you>, "
                f"user_id=\"{owner_user_id}\", content=<summary + peer "
                "quote>)`. Make the summary actionable: what did the peer "
                "say, what does it mean for the owner's task, what's next."
            )
            lines.append(
                "3. If the peer needs a clarifying follow-up from you → "
                "send it via `message_agent`, THEN also call "
                "`notify_owner` with a short status "
                "update (\"asked peer for X, waiting for clarification\") "
                "so the owner knows the thread is alive."
            )
            lines.append(
                "4. Silence is the wrong default. Only stay silent if the "
                "peer message is truly irrelevant (e.g. a closing "
                "acknowledgment you already reported to the owner)."
            )

        return "\n".join(lines)

    async def _invoke_runtime(
        self,
        agent_id: str,
        sender_agent_id: str,
        prompt: str,
        channel_id: str,
        trigger_message_id: str = "",
        retrieval_anchor: str = "",
        errand_continuation: bool = False,
        on_progress=None,
        on_event_id=None,
        team_room: bool = False,
        patrol: bool = False,
        include_monologue: bool = False,
        team_id: str = "",
        cancellation=None,
        root_run_id: str = "",
        steering=None,
        probe_token: Optional[str] = None,
    ) -> TurnResult:
        """
        Invoke AgentRuntime.run() for the given agent with the prompt.

        ``include_monologue`` is PATROL'S, and only patrol's. A team REPLY is a
        tool call now (`message_team`), so nothing harvests an agent's plain text
        as a message any more — except the patrol line, which is a different act:
        the platform asks the lead to compose the room's status line and then
        posts it under the ROOM's own marker with `msg_type=patrol`. A tool could
        not do that (a tool posts as the agent, and the line would then count as
        an agent hop and read as the lead chatting).

        NexusPower streams an agent's plain text as AGENT_THINKING with the
        monologue subset set, so without this flag `turn.text` is EMPTY on that
        framework — dropping it would have made patrol silently stop working for
        every nexus_power agent while looking fine on claude_code.

        Returns a ``TurnResult``.

        ``segments_sink`` is NOT a parameter here any more, and the paragraph
        that used to describe it is gone with it: the team lane stopped
        harvesting an agent's plain text when the room became a tool call, so
        there is nothing to accumulate mid-run. `run_collector` still accepts the
        argument for its own callers.

        ``probe_token`` is the circuit-breaker half-open probe claim the
        caller won for this turn (lane or patrol), or None. It goes to
        ``run_and_collect``, which settles the probe from the run's outcome
        through the same ``record_success`` / ``record_failure`` the WS path
        uses — the bus paths have no other breaker feed.

        ``errand_continuation`` is the DM classifier's verdict ("this batch
        answers an errand I started"). When true, this turn's ERRAND SCOPE
        (the peer that answered us + the channel it happened in) rides
        ``trigger_extra_data`` into ``ctx_data.extra_data``, so a bus send
        aimed at that peer can stamp itself ``BUS_ERRAND_TURN_SOURCE`` instead
        of the plain "message_bus" — otherwise a clarifying follow-up sent
        from this turn is indistinguishable from an answer and the RECIPIENT's
        classifier routes it to Owner Relay (P1 recurrence, path A of the
        2026-08-03 review).

        The scope, not a whole-turn flag: bus unread is injected from ALL
        channels every turn and the module prompt requires answering it, so
        this same turn routinely also answers an unrelated peer. Stamping the
        turn marked that answer as a question too, and that peer then stopped
        relaying to ITS owner — the same P1, one seat over (same review). Only
        the send site knows its target, so only the send site can decide; see
        ``_message_bus_mcp_tools._send_turn_source``.

        Returns a :class:`TurnResult` — the collected response text, the
        turn's events-row id (None if the run died before Step 0), whether any
        reply tool actually delivered, and whether the run ended fatal. The
        team branch stamps the id onto the reply it posts back into the room,
        so the transcript can open that turn's event_log; the caller reads
        ``delivered`` to tell a turn that answered its peer through a tool
        apart from one that reached nobody, and ``fatal`` to tell whether
        ``text`` is the agent's words or a failure notice standing in for
        them — a distinction the room's own error surface depends on.

        `on_event_id`, when provided (team branch only), is forwarded to
        `collect_run` so the turn's events-row id gets bound onto the
        activity row for the team UI.

        There is NO plain-text deliverer any more. The room used to be handed
        the turn's plain text by the runtime; it takes a tool call now, so this
        method's only remaining question about a team turn is whether the agent
        posted — which the bus can answer directly.

        Raises:
            RuntimeError: If AgentRuntime cannot be imported or execution fails.
        """
        try:
            from narranexus.platform.agent_runtime.client import (
                get_agent_runtime_client,
            )
        except ImportError as e:
            raise RuntimeError(
                f"Cannot import AgentRuntime dependencies: {e}"
            ) from e

        collection = await get_agent_runtime_client().run_and_collect(
            agent_id=agent_id,
            user_id=sender_agent_id,
            input_content=prompt,
            working_source=WorkingSource.MESSAGE_BUS,
            # PATROL ONLY (see the parameter's note in `_invoke_runtime`).
            include_monologue=include_monologue,
            # Team rooms only: one in-turn nudge if the turn is about to end
            # without having spoken.
            #
            # The PRIMARY net is not this — it is that `message_team` is the
            # turn's DECLARED default reply tool, which both frameworks' reply
            # reminders render. This is the extra one, and it only exists on
            # NexusPower (`loop.py` STOP_CHECK); a claude_code agent gets the
            # reminder and the after-the-fact room notice, not the nudge.
            #
            # Deliberately NOT the helper-LLM fallback that the IM DM lane uses.
            # That path has a helper write the reply and the platform deliver it
            # — acceptable in a 1:1 thread, wrong in a team room, where it would
            # be the platform impersonating an agent in front of its teammates
            # and its owner. The room is told the turn said nothing instead
            # (`_announce_failed_room_post`), and the words stay the agent's.
            #
            # A minimal profile, not `fast_for(...)`: every other field's default
            # preserves current behaviour, and `narrative_persistence` is read
            # only on the `bm25_top1` fast path, which this does not take.
            # `expression_nudge` is the MESSAGE lane's, not patrol's. It fires
            # NexusPower's mute-turn repair when a turn closes having called no
            # reply tool — correct when the room expects `message_team`, exactly
            # wrong on patrol, where closing with plain text (or in silence) is
            # the specified outcome and the nudge would name a tool the patrol
            # prompt forbids.
            turn_profile=(
                TurnProfile(name="team_room", expression_nudge=True)
                if team_room and not patrol else None
            ),
            on_progress=on_progress,
            on_event_id=on_event_id,
            # Rides the extra_kwargs seam straight to AgentRuntime.run — no
            # signature change anywhere in between (collect_run's docstring
            # names `cancellation` as a supported pass-through). Until now
            # this was always the runtime's own no-op token, which is why a
            # bus run could not be stopped from anywhere.
            cancellation=cancellation,
            # Settled by the client after the run's events row is terminal.
            probe_token=probe_token,
            # Same explicit seam as cancellation: a live SteerChannel this run
            # drains at each step boundary. None on non-steerable runs (no
            # mid-turn injection). See run_registry / steer_channel.
            steering=steering,
            # Same seam. A team room's reply has to be POSTED inside the turn:
            # the chat rows are written by persist_turn before run()
            # returns, so a post that lands after it cannot be recorded as a
            # reply — which is why every team turn used to file as "no reply
            # sent" and start the next one cold.
            trigger_extra_data={
                "bus_channel_id": channel_id,
                "retrieval_anchor": retrieval_anchor,
                # Default-reply marker: on a team-room turn the DEFAULT reply
                # verb is `message_team`, not the peer `message_agent`.
                # MessageBusModule reads it (expressive_tools) to point the
                # reply reminder at message_team. It no longer removes the peer
                # verb: every internal send verb stays reachable on every turn
                # (capability follows the agent, not the trigger channel).
                # Deleting this marker only flips which verb the reminder
                # defaults to — it does not change what the agent can reach, and
                # the patrol marker below is what still clears the desk.
                BUS_TEAM_ROOM_EXTRA_KEY: team_room,
                # Patrol delivers by speaking: the platform posts the composed
                # line under the room's own marker. The module reads this to
                # declare nothing and clear both send verbs off the desk.
                BUS_PLAIN_TEXT_TURN_EXTRA_KEY: patrol,
                # Errand scope — empty unless this turn continues our own
                # errand. sender_agent_id is the peer whose reply triggered us,
                # i.e. exactly who a follow-up would go to.
                "bus_errand_peer": sender_agent_id if errand_continuation else "",
                "bus_errand_channel": channel_id if errand_continuation else "",
                # Trigger tree. Two consumers, both needed: the client hands it
                # to RunRecorder (so this run's events row joins the tree) and
                # context_runtime injects it into the MCP identity headers (so a
                # send from this turn stamps the next message and the tree
                # survives the next hop).
                "root_run_id": root_run_id,
                # The team whose room this turn runs in ("" outside a team).
                # The trigger already resolved it above; publishing it here is
                # what lets tools learn the team from the SERVER rather than
                # from a model-filled parameter (see module/_mcp_identity.py).
                # Not folded into bus_errand_channel: that one is stamped only
                # for errand continuations, so it is empty on most team turns.
                "bus_team_id": team_id,
                "trigger_id": (
                    f"bus_{trigger_message_id}"
                    if trigger_message_id
                    else f"bus_chan_{channel_id}"
                ),
            },
        )

        # Error path (Bug 2): previously the loop only checked
        # AGENT_RESPONSE; if the agent run errored (e.g. owner removed
        # their provider, system default exhausted) the sender agent got
        # an empty string and had to guess why. Now we surface the error
        # inline so the sender sees what went wrong.
        if collection.is_error:
            logger.warning(
                f"[MessageBusTrigger] agent {agent_id} run reported an error in "
                f"channel {channel_id}: {collection.error.error_type}: "
                f"{collection.error.error_message} "
                f"(severity={collection.error.severity or 'unlabelled'})"
            )
        # FATAL only. `is_error` is set by ANY error frame, recoverable ones
        # included — and a recoverable hiccup is one the loop absorbed before
        # going on to answer correctly. Reading it as "the turn failed" replaced
        # that real reply with a failure notice, so one provider wobble cost the
        # sender their answer.
        if collection.is_fatal:
            return TurnResult(
                text=(
                    f"⚠️ I couldn't process your message right now "
                    f"({collection.error.error_type}). "
                    f"{collection.error.error_message}"
                ),
                event_id=collection.event_id,
                fatal=True,
            )

        return TurnResult(
            text=collection.output_text,
            event_id=collection.event_id,
            delivered=self._delivered_to_anyone(collection.tool_calls),
        )

    @staticmethod
    def _delivered_to_anyone(tool_calls: Optional[List[str]]) -> bool:
        """Did any tool this turn called actually reach a recipient?

        Asks the MessageSource registry rather than matching names here: it
        already owns the list of tools that DELIVER on a bus turn (the two bus
        sends plus the owner-relay), and a second copy would drift from it the
        day a third one appears — the precise way the 2026-08-01 no-reply
        metric got poisoned.

        Fails to **True**. The consumer of a False is a public "this turn
        delivered nothing" line, so a registry hiccup would print that lie
        underneath a reply that landed. Missing a real silence costs the user
        nothing they were not already living with; inventing one costs exactly
        the trust this whole change exists to rebuild.

        ``MessageSourceRegistry.get`` NEVER raises — it silently falls back to
        the default handler (the two owner-facing tools only) for an
        unregistered source,
        which is a second, quieter failure mode than the exception this
        try/except covers. Both must fail open to True: a downgraded registry
        that no longer recognises ``message_team`` would otherwise stamp
        "no reply" under every turn that answered its peer correctly.
        """
        try:
            from narranexus.platform.channel.message_source_handler import (
                MessageSourceRegistry,
            )

            handler = MessageSourceRegistry.get(WorkingSource.MESSAGE_BUS.value)
            if handler.name != WorkingSource.MESSAGE_BUS.value:
                # The bus handler is absent from ``ingress.message_sources``
                # (builtin.message_bus disabled or excluded, or a process that
                # never booted the plugin platform).
                # Treat it as "we can't prove delivery", never as "nothing was
                # delivered" — the latter is the lie this whole change fights.
                logger.warning(
                    "message_bus message source not registered (ingress.message_sources) "
                    f"(got {handler.name!r}); delivery detection assuming delivered"
                )
                return True
            return any(
                handler.is_user_reply_tool(name or "") for name in tool_calls or ()
            )
        except Exception as e:  # noqa: BLE001 — see the fail-to-True note above
            logger.warning(f"delivery detection failed, assuming delivered: {e}")
            return True

    async def _announce_failed_room_post(
        self, agent_id: str, channel_id: str, trigger_message: BusMessage,
        turn: "TurnResult", error: Exception | str,
    ) -> None:
        """The reply exists, the room never got it. Say so, and keep the reply.

        `error` is the write's exception when the post was tried and failed, or
        a plain reason when it was never tried at all — the remedy is the same
        either way, and which of the two it was is the notice's content, not a
        different code path.

        Two separate losses, so two separate remedies:

        * the room shows a delivery-failure line, because a user staring at an
          unchanged room otherwise concludes the agent ignored them — with the
          backend green and the run billed;
        * the reply text goes to the owner's inbox, because it was generated
          and paid for and one failed write is no reason to destroy it.

        The notice travels the very path that just failed, so it failing too
        is the expected case rather than an edge one — hence best-effort, and
        hence the inbox write happening regardless of whether it landed.
        """
        logger.warning(
            f"MessageBusTrigger: reply never reached the room for agent "
            f"{agent_id} in {channel_id}: {error}"
        )
        await announce_delivery_failure(
            self._bus, channel_id, agent_id,
            error=str(error),
            root_run_id=trigger_message.root_run_id or None,
        )
        await self._write_to_inbox(
            agent_id, channel_id, trigger_message, turn.text
        )

    @staticmethod
    def _receipt_worthy(messages: List[BusMessage]) -> List[BusMessage]:
        """The messages in a batch a SENDER is waiting on a receipt for: peer
        agents' own messages. A person's message has no agent turn behind it,
        and a platform line is nobody's question."""
        return [
            m for m in messages
            if m.from_agent
            and not m.from_agent.startswith(USER_SENDER_PREFIX)
            and (m.msg_type or "") not in PLATFORM_MSG_TYPES
        ]

    @classmethod
    def _batch_fingerprint(cls, batch: List[BusMessage]) -> Tuple[str, List[str]]:
        """``(content_key, message_ids)`` for ONE batch — the single definition
        the receipt writer and both resend guards share (#389 I4).

        Writer and readers must agree byte for byte or the guards go silent
        with every test still green (each test walks one path). The key is
        the worthy messages' contents joined by a newline; the ids expand
        every part row a merged message stands for. Empty key when nothing
        in the batch is a peer message worth a receipt.
        """
        worthy = cls._receipt_worthy(batch)
        if not worthy:
            return "", []
        from narranexus.platform.repository.bus_delivery_receipt_repository import (
            content_key,
        )

        return (
            content_key("\n".join(m.content for m in worthy)),
            [mid for m in worthy for mid in (m.part_message_ids or [m.message_id])],
        )

    async def _stamp_receipts(
        self,
        messages: List[BusMessage],
        agent_id: str,
        channel_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None:
        """Write what became of this batch at this recipient into
        `bus_delivery_receipts` — one row per message, upserted. Best-effort,
        never raises: a ledger write must not turn a delivered turn into a
        recorded failure. `reason` arrives already redacted.

        DM lanes only (the callers gate on `not is_team`): a team room's
        senders are watching the room itself, and a receipt per mention per
        member would be a write per turn nobody reads.
        """
        worthy = self._receipt_worthy(messages)
        if not worthy:
            return
        try:
            from narranexus.platform.repository.bus_delivery_receipt_repository import (
                BusDeliveryReceiptRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            repo = BusDeliveryReceiptRepository(await get_db_client())
            # One fingerprint per BATCH (not per message): a resend of the
            # same batch must match across batches.
            key, _ids = self._batch_fingerprint(messages)
            for m in worthy:
                for message_id in (m.part_message_ids or [m.message_id]):
                    await repo.upsert(
                        message_id=message_id, to_agent=agent_id,
                        channel_id=channel_id, from_agent=m.from_agent,
                        status=status, reason=reason, attempts=attempts,
                        content_key=key,
                    )
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.warning(
                f"[bus-receipt] could not stamp {status} for {agent_id} in "
                f"{channel_id}: {e}"
            )

    async def _wake_sender_on_drop(
        self, agent_id: str, channel_id: str, trigger_message: BusMessage,
        *, error: str, attempts: int, batch: Optional[List[BusMessage]] = None,
    ) -> None:
        """The message is gone for good: say so where the SENDER will see it.

        `_notify_permanent_failure` tells the recipient's owner. The sender —
        another agent, mid-errand, that has just told its own user the work
        is under way — was told nothing (upstream NetMindAI-Open/NarraNexus#106),
        and kept waiting. This posts a `system_delivery_failed` line into the
        conversation with the sender mentioned, so its next turn opens on the
        failure (labelled by `system_messages.trigger_label`) and it can retry,
        route around, or tell its user. Only when the sender is an agent: a
        person's message has no turn to wake, and the owner inbox is theirs.

        When the guard itself cannot be read the wake goes out (fail-open) AND
        no window is armed — a second fail-open, accepted because an
        unreadable table would refuse the arm too; the next readable poll
        re-establishes the window.
        """
        sender = trigger_message.from_agent or ""
        if not sender or sender.startswith(USER_SENDER_PREFIX):
            return
        if (trigger_message.msg_type or "") in PLATFORM_MSG_TYPES:
            return
        # Windowed, two layers, never a loop (review C2): the notice wakes the
        # sender, a model that reads "could not process" resends or rephrases,
        # the recipient drops that too — and without a bound this DM would
        # run thousands of rounds a day while the recipient stays broken (the
        # 2026-08-17 Liam ping-pong, lit by the platform itself). The receipt
        # ledger recognises the SAME content dropped again; the cooldown row
        # (recipient, channel, "peer_drop") bounds rephrasings. Both share
        # FAILURE_NOTIFY_COOLDOWN_SECONDS so the recipient's owner notice and
        # the sender's wake expire together, and a recipient fixed-then-broken
        # again is reported anew.
        from narranexus.platform.repository.owner_notice_cooldown_repository import (
            OwnerNoticeCooldownRepository,
        )
        from narranexus.platform.utils.db.db_factory import get_db_client

        try:
            db = await get_db_client()
            cooldowns = OwnerNoticeCooldownRepository(db)
            if await cooldowns.is_cooling(
                agent_id, channel_id, "peer_drop", FAILURE_NOTIFY_COOLDOWN_SECONDS
            ) or await self._drop_already_announced(
                agent_id, channel_id, batch or [trigger_message]
            ):
                logger.info(
                    f"[bus-drop] {agent_id} dropped another message from {sender} "
                    f"in {channel_id} inside the window; not waking it again"
                )
                return
        except Exception as e:  # noqa: BLE001 — fail open: one extra wake beats a hidden drop
            logger.warning(f"[bus-drop] guard read failed for {agent_id}: {e}")
            cooldowns = None
        # Both awaits run inside the caller's `except` handler; an exception
        # here would escape it (#389 M3). Best-effort like every notice.
        try:
            landed = await announce_processing_failure(
                self._bus, channel_id, agent_id,
                error=error, attempts=attempts, mentions=[sender],
                root_run_id=trigger_message.root_run_id or None,
            )
            if landed and cooldowns is not None:
                await cooldowns.arm(agent_id, channel_id, "peer_drop")
        except Exception as e:  # noqa: BLE001 — a notice may never become the new failure
            logger.warning(f"[bus-drop] could not wake {sender} in {channel_id}: {e}")

    async def _drop_already_announced(
        self, agent_id: str, channel_id: str, batch: List[BusMessage],
    ) -> bool:
        """Was this exact content already dropped by this recipient in this
        channel within the window, on an earlier message?

        Fingerprints the WHOLE batch, the same way `_stamp_receipts` wrote it
        (review r2 I1: keying on the trigger message alone never matched a
        two-message batch's receipts, so this layer silently did nothing).
        The current batch's own `dropped` rows — written just before this,
        one per message (and per part) — are all excluded by id, or a
        two-message batch would suppress its own first wake.
        """
        from narranexus.platform.repository.bus_delivery_receipt_repository import (
            BusDeliveryReceiptRepository,
        )
        from narranexus.platform.utils.db.db_factory import get_db_client

        key, own_ids = self._batch_fingerprint(batch)
        if not key:
            return False
        return await BusDeliveryReceiptRepository(await get_db_client()).prior_outcome(
            channel_id=channel_id, to_agent=agent_id,
            key=key,
            status=RECEIPT_DROPPED,
            exclude_message_ids=own_ids,
            within_seconds=FAILURE_NOTIFY_COOLDOWN_SECONDS,
        )

    async def _announce_undelivered_turn(
        self, agent_id: str, channel_id: str, trigger_message: BusMessage,
        *, is_team: bool, errand_continuation: bool,
        batch: Optional[List[BusMessage]] = None,
    ) -> None:
        """The turn ran and reached nobody. Make that visible.

        ``batch`` is what the turn was built from; on a DM it decides whether
        the asking peer is woken (see the resend guard below).

        WHO is left waiting decides who gets woken, and the two surfaces
        differ:

        * team room — nobody is blocked, and NOTHING is posted. Until
          2026-09-03 a mention-less platform line went into the transcript so
          readers could tell "said nothing" from "never ran"; the same prompt
          told the agent silence was legitimate, so the line read as a
          reprimand for obeying it, and agents filled the room to avoid it.
          The fact now lives on the member's activity row instead
          (`activity.note_silent_turn` → roster "last turn: said nothing").
        * A2A DM, and we are the one being ASKED — the peer IS blocked, and
          only a message wakes it. It gets mentioned, which is how an errand
          that would otherwise hang forever resolves itself.
        * A2A DM, but this batch was a REPLY to our own errand — the peer
          already answered and is waiting for nothing; our OWNER is the one
          left hanging, so the inbox notice below is the whole remedy.

        Never announces in answer to an announcement: two quiet agents would
        otherwise volley platform lines at each other, each silence provoking
        the next notice. Guarded against EVERY platform type, not only the
        undelivered notice itself — a patrol line also mentions the members it
        is chasing, and one of them going quiet must not then read as "the user
        asked and got nothing". Platform-initiated turns have no one waiting on
        an answer, so their silence is not a silence we owe the user an
        explanation for.

        And never wakes the asker TWICE for the same question (2026-09-09).
        The notice is itself a message in a DM, so it starts the asker's next
        turn; a model that reads "ended without replying" and simply sends the
        same text again gets the same silence, which gets the same notice —
        the 8/31 ping-pong. The receipt ledger remembers the content
        fingerprint of every batch this agent went silent on
        (`_stamp_receipts` → `content_key`), so a silence on a RESEND is
        recorded but posts nothing: the asker's owner already has the inbox
        notice, and the asker is not woken into round three.
        """
        if (trigger_message.msg_type or "") in PLATFORM_MSG_TYPES:
            return

        if is_team:
            # No owner inbox notice either: the owner IS in this room and the
            # roster shows the mark (with the drawer open). A DM silence
            # happens where nobody is watching, which is what the inbox
            # notice below is for.
            from narranexus.platform.message_bus import _bus_activity
            from narranexus.platform.utils.db.db_factory import get_db_client

            await _bus_activity.note_silent_turn(
                await get_db_client(), agent_id, channel_id
            )
            # The transcript no longer counts these; the log still can
            # (`grep "\[team-turn\] silent"` replaces the old
            # `msg_type='system_undelivered'` count).
            logger.info(
                f"[team-turn] silent agent={agent_id} channel={channel_id} "
                f"trigger={trigger_message.message_id}"
            )
            return

        sender = trigger_message.from_agent or ""
        wake_peer = (
            not errand_continuation
            and bool(sender)
            and not sender.startswith(USER_SENDER_PREFIX)
        )
        # Two bounds, mirroring the drop path (#389 I2): the content
        # fingerprint stops a verbatim resend, and a (recipient, channel)
        # window under its OWN category — never the owner-inbox "no_reply"
        # window, which would let "the owner was told" swallow "the sender
        # was never woken" — stops a REPHRASED one from waking the sender
        # every turn. Gated on the peer being an AGENT (a person's silence
        # must not occupy the window), not on `wake_peer`: in a DM every row
        # starts the other member's turn, mention or not, so an unmentioned
        # notice on an errand-continuation batch wakes the peer just the same.
        peer_is_agent = bool(sender) and not sender.startswith(USER_SENDER_PREFIX)
        cooldowns = None
        if peer_is_agent:
            from narranexus.platform.repository.owner_notice_cooldown_repository import (
                OwnerNoticeCooldownRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            try:
                cooldowns = OwnerNoticeCooldownRepository(await get_db_client())
                if await cooldowns.is_cooling(
                    agent_id, channel_id, "no_reply_peer", FAILURE_NOTIFY_COOLDOWN_SECONDS
                ):
                    logger.info(
                        f"[bus-resend] {agent_id} silent again towards {sender} in "
                        f"{channel_id} inside the window; not waking it again"
                    )
                    await self._notify_undelivered_owner(agent_id, channel_id, sender)
                    return
            except Exception as e:  # noqa: BLE001 — fail open: one extra wake beats a hidden silence
                logger.warning(f"[bus-resend] window read failed for {agent_id}: {e}")
                cooldowns = None
        if await self._silence_already_announced(
            agent_id, channel_id, batch or [trigger_message]
        ):
            logger.info(
                f"[bus-resend] {agent_id} silent again on the same content in "
                f"{channel_id}; not waking {sender or 'the sender'} a second time"
            )
            await self._notify_undelivered_owner(agent_id, channel_id, sender)
            return
        landed = await announce_undelivered(
            self._bus, channel_id, agent_id,
            mentions=[sender] if wake_peer else None,
            root_run_id=trigger_message.root_run_id or None,
        )
        if landed and peer_is_agent and cooldowns is not None:
            with contextlib.suppress(Exception):
                await cooldowns.arm(agent_id, channel_id, "no_reply_peer")
        # A DM silence happens somewhere nobody is watching, so the owner only
        # ever learns of it here.
        await self._notify_undelivered_owner(agent_id, channel_id, sender)

    async def _silence_already_announced(
        self, agent_id: str, channel_id: str, batch: List[BusMessage],
    ) -> bool:
        """Has this agent already gone silent on exactly this content, in this
        channel, on an earlier message? Reads the receipt ledger; fails to
        False (announce) — a missed dedup costs one extra wake, a false one
        would hide a fresh silence."""
        key, own_ids = self._batch_fingerprint(batch)
        if not key:
            return False
        try:
            from narranexus.platform.repository.bus_delivery_receipt_repository import (
                BusDeliveryReceiptRepository,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            return await BusDeliveryReceiptRepository(await get_db_client()).prior_outcome(
                channel_id=channel_id, to_agent=agent_id,
                key=key,
                status=RECEIPT_SILENT,
                exclude_message_ids=own_ids,
                within_seconds=FAILURE_NOTIFY_COOLDOWN_SECONDS,
            )
        except Exception as e:  # noqa: BLE001 — see docstring
            logger.warning(f"[bus-resend] guard read failed for {agent_id}: {e}")
            return False

    async def _notify_undelivered_owner(
        self, agent_id: str, channel_id: str, sender: str,
    ) -> None:
        """Tell the owner their agent burned a turn and delivered nothing.

        Its own notice rather than a `_write_to_inbox` row: that writer's
        title and MESSAGE_BUS type say "your agent relayed something from a
        peer", which is the opposite of what happened here.

        Cooldown keyed per (agent, channel) with category `no_reply`, not per
        message: the fix is "look at what this agent keeps doing", so a busy A2A
        channel where the agent goes quiet every turn must not flood the inbox
        with one identically-titled row per incoming message. Per channel rather
        than per agent since 2026-09-09: silence towards peer B is a different
        fact from silence towards peer C, and the notice names the channel.
        """
        await self._notify_owner(
            agent_id,
            title=f"No reply delivered: {agent_id}",
            content=(
                f"Your agent ran a turn for a message from {sender or 'a peer'} "
                f"on channel {channel_id} and finished without delivering "
                f"anything — no reply to the sender and nothing to you.\n\n"
                f"The sender has been told, so it can ask again or route "
                f"around it. Check the agent's recent activity to see what "
                f"the turn did instead."
            ),
            source_type="message_bus_no_reply",
            channel_id=channel_id,
            message_id_prefix="busnorep_",
            cooldown_category="no_reply",
        )

    async def _write_to_inbox(
        self, agent_id: str, channel_id: str,
        trigger_message: BusMessage, agent_response: str,
    ) -> None:
        """Write the agent's response to the recipient user's inbox.

        Uses `InboxRepository.create_message` (the canonical writer) so
        the row shape stays in sync with the `inbox_table` schema —
        previous hand-written `db.insert("inbox_table", ...)` referenced
        `agent_id` / `owner_user_id` / `updated_at` columns that don't
        exist and omitted the required `message_id`, producing
        `Unknown column 'agent_id' in 'field list'` 13 times in 3
        hours on EC2 2026-05-18.
        """
        try:
            import uuid

            from narranexus.platform.repository.inbox_repository import InboxRepository
            from narranexus.platform.schema.inbox_schema import (
                InboxMessageType,
                MessageSource,
            )
            from narranexus.platform.utils.db.db_factory import get_db_client

            db = await get_db_client()
            agent_row = await db.get_one("agents", {"agent_id": agent_id})
            if not agent_row:
                logger.warning(f"Cannot write to inbox: agent {agent_id} not found")
                return
            recipient_user_id = agent_row.get("created_by", "")
            if not recipient_user_id:
                logger.warning(
                    f"Cannot write to inbox: agent {agent_id} has no created_by"
                )
                return

            repo = InboxRepository(db)
            await repo.create_message(
                user_id=recipient_user_id,
                message_id=f"bus_{uuid.uuid4().hex[:16]}",
                title=f"Message Bus: {trigger_message.from_agent}",
                content=agent_response,
                message_type=InboxMessageType.MESSAGE_BUS,
                source=MessageSource(type="message_bus", id=channel_id),
            )
            logger.info(
                f"Wrote MessageBus result to inbox for user {recipient_user_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")


async def _get_bus() -> LocalMessageBus:
    """Create and return a LocalMessageBus instance from environment config.

    Works with both SQLite (local) and MySQL (cloud) backends — LocalMessageBus
    is a misnomer; it's a database-backed bus that runs against any backend.
    """
    from narranexus.platform.utils.db.db_factory import get_db_client

    db = await get_db_client()
    backend = db._backend

    # Ensure all tables exist (schema_registry covers all 26 tables including bus)
    from narranexus.platform.utils.db.schema_registry import auto_migrate
    await auto_migrate(backend)

    # Initialise the system-default quota subsystem so bus-triggered
    # agent turns can fall back to the free-tier config when the owner
    # hasn't configured their own provider.

    return LocalMessageBus(backend=backend)


async def main() -> None:
    """Entry point for standalone execution."""
    logger.info("Starting MessageBusTrigger...")
    bus = await _get_bus()
    trigger = MessageBusTrigger(bus=bus)

    try:
        await trigger.start()
    except KeyboardInterrupt:
        trigger.stop()
        logger.info("MessageBusTrigger stopped by user")


if __name__ == "__main__":
    from narranexus.platform.utils.logging import setup_logging
    setup_logging("message_bus_trigger")
    try:
        asyncio.run(main())
    finally:
        asyncio.run(logger.complete())
