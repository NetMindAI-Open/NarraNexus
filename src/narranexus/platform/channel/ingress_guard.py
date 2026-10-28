"""
@file_name: ingress_guard.py
@author:
@date: 2026-08-24
@description: Channel-agnostic ingress circuit breaker — "is this message
worth processing at all?"

Written for the 2026-08-17 ingress design after the 8/14 DM ping-pong
incident (70+ hours, 66k messages, monitoring entirely green). The
inbound path had message-IDENTITY dedup and burst merging, but nothing
that looked at what was actually arriving: every message ran the full
pipeline (narrative retrieval, persona update, agent loop, reply
decision). When the far side is a broken agent reciting the same line,
every layer is individually correct and the whole is a perpetual motion
machine.

This guard is the L2/L3 half of that design — a HARD breaker. The L0
observation tier and L1 merge tier land with the unified ingress in the
IM/Bus refactor. Their absence is covered by making the entry condition
conservative rather than by shipping a lenient breaker: a session must be
BOTH fast AND repetitive. Normal high-frequency conversation — a user
firing off six thoughts, a busy group at peak, a job batch — varies its
content and is structurally immune, which is what the L0 observation
window was there to protect.

Relationship to the two breakers that already exist:
  - ``channel_trigger_base``'s fast-death breaker → "my credential is
    broken, stop restarting the subscriber"
  - ``agent_framework/loop/circuit_breaker`` → "my turns keep failing,
    stop scheduling them"
  - **this one** → "the traffic arriving is not worth processing"
All three share the escalate-cooldown-then-half-open paradigm; none
subsumes another.

Binding rules #14/#15: this gates INGRESS. It never caps, cancels, or
time-limits a running ``agent_loop``, and it never judges the agent's own
model, verbosity, or output — only the shape of inbound traffic.

Clock: one clock, wall-clock, injectable. The credential breaker can use
``time.monotonic()`` because it is memory-only; a cooldown that must
survive a restart has to be expressed in wall time, and running two
clocks in one state machine is how off-by-one-restart bugs are born.
Callers omit ``now``; tests pass it explicitly so every temporal
assertion is arithmetic rather than sleeping.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, Optional, Tuple

from loguru import logger

from narranexus.platform.schema.channel_ingress_breaker_schema import (
    ChannelIngressBreaker,
    session_key,
)
from narranexus.platform.utils.timezone import utc_now

# ── Defaults ──────────────────────────────────────────────────────────
# A session must clear BOTH the rate bar and the repetition bar. Agent
# peers get tighter numbers on both: A2A rooms are where the loops live,
# and no human types the same sentence twenty times in ten minutes.
DEFAULT_WINDOW_SECONDS = 600.0
DEFAULT_RATE_THRESHOLD = 60
DEFAULT_DUP_RATIO_THRESHOLD = 0.8
DEFAULT_AGENT_RATE_THRESHOLD = 20
DEFAULT_AGENT_DUP_RATIO_THRESHOLD = 0.5

# 5min → 30min → 2h → 24h, last step repeating forever. Deliberately a
# literal schedule rather than ``utils/backoff.compute_cooldown_seconds``
# (base·2^(n-1)) — these four numbers come from the design doc and no
# doubling formula produces them.
DEFAULT_SCHEDULE_SECONDS: Tuple[float, ...] = (300.0, 1800.0, 7200.0, 86400.0)

# How many consecutive clean windows walk the tier back down one step.
DEFAULT_RECOVERY_WINDOWS = 2

# Hard ceiling on the per-session event ring. A session at the ceiling has
# already tripped many times over; the cap only stops a pathological
# sender from growing the deque without bound between evictions.
_MAX_EVENTS_PER_SESSION = 4096

# How often (in admitted messages) to sweep idle sessions out of memory.
#
# Without this the guard leaks: every (channel, chat_id, sender_id) it ever
# sees keeps a _SessionState forever, each holding a deque. A person who
# says one thing and never returns costs the same as a live conversation.
# Long-running trigger processes are a first-class scenario (binding rule
# #14), so "it only matters after a few hundred hours" is not a defence —
# that is exactly the uptime these processes are designed for.
#
# Amortised rather than per-message: the sweep is O(sessions), so paying it
# once every N messages keeps the hot path O(1). It runs on both the native
# and the managed path because it lives in admit() — the managed
# coordinator has no cleanup tick of its own to hang it on.
_PRUNE_EVERY_ADMITS = 1000

# Cap on the fingerprint set carried from a trip into its half-open probe.
# A repeat storm is low-cardinality by definition (that is what made it a
# storm), so this only bounds the pathological case.
_MAX_TRIP_FINGERPRINTS = 64


def content_fingerprint(chat_id: str, sender_id: str, content: str) -> str:
    """Stable identity of a message's CONTENT within one conversation.

    Scoped by ``(chat_id, sender_id)`` so "ok" from two different people
    in two different rooms are three different things. Empty content
    yields an empty fingerprint, which the guard treats as *unique*, not
    as a repeat — a caption-less file upload must never read as a
    verbatim-repeat storm (same carve-out the empty-content guard makes
    for ``raw["attachment_refs"]``).

    Exact-match today. Near-duplicate detection (simhash) is a later
    upgrade; verbatim recital is what the 8/14 incident actually was, and
    an exact hash catches it with zero false positives.

    This is the unconditional form. ``ChannelTriggerBase._content_fingerprint``
    wraps it with the ``CONTENT_DEDUP_WINDOW_SECONDS`` opt-in that governs
    the dedup-store layer — that gate is about a different question and
    stays where it is.
    """
    if not content:
        return ""
    material = f"{chat_id}|{sender_id}|{content}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# Every transition worth a durable row. Kept as data rather than as four
# string literals scattered through the state machine: ``audit_event()``
# maps these to audit events, and an unmapped value fails silently — both
# receive faces simply write nothing. A test asserts the set is fully
# mapped, so adding a transition without mapping it goes red here instead
# of going missing from the DB.
TRANSITION_TRIPPED = "tripped"
TRANSITION_ESCALATED = "escalated"
TRANSITION_PROBE = "probe"
TRANSITION_RECOVERED = "recovered"
INGRESS_TRANSITIONS: frozenset = frozenset(
    {
        TRANSITION_TRIPPED,
        TRANSITION_ESCALATED,
        TRANSITION_PROBE,
        TRANSITION_RECOVERED,
    }
)


@dataclass(frozen=True)
class IngressVerdict:
    """One decision, plus the evidence behind it.

    The evidence fields are not decoration: every drop and every
    transition has to be answerable from the DB later (incident lessons
    #3/#5), so the caller writes these straight into the audit row rather
    than re-deriving numbers the guard already computed.
    """

    admit: bool
    session_key: str
    tier: int = 0
    reason: str = "ok"
    # None for the ordinary case; otherwise one of ``INGRESS_TRANSITIONS``
    # — the moments worth an audit row and a log line. The set is data, not
    # a comment, so a test can assert that every member maps to an event:
    # an unmapped value returns None from ``audit_event()`` and then
    # NEITHER receive face writes a row, which is silent by construction.
    transition: Optional[str] = None
    window_count: int = 0
    dup_ratio: float = 0.0
    # The length assigned when this isolation started. Set on a trip; 0.0
    # elsewhere because no isolation started there.
    cooldown_seconds: float = 0.0
    # How much longer this conversation stays suppressed, as of this
    # verdict. One caliber on every row: the full length on the trip that
    # opened it, what is left on each message it swallows, 0.0 when
    # nothing is being suppressed. Without it a drop row says "silenced"
    # but not "until when" — and "the bot went quiet, for how long?" is
    # the question these rows exist to answer.
    cooldown_remaining_seconds: float = 0.0
    suppressed: int = 0
    is_agent_peer: bool = False

    def audit_event(self) -> Optional[str]:
        """Which audit event this verdict is, or None when it is not worth
        a row.

        Lives here rather than at the call sites because there are two of
        them — the native trigger and the managed coordinator — and they
        must WRITE separately (different repositories, and the coordinator
        deliberately does not go through the trigger) but must not each own
        a copy of WHICH event a transition is. The design still has an
        observe tier and a merge tier unbuilt, so ``transition`` will grow
        values; a copy that does not grow with it means those events simply
        do not exist for one of the two surfaces.

        The quiet "ok" case writes nothing — already covered by
        ``ingress_processed``.
        """
        from narranexus.platform.channel.channel_audit_events import (
            EVENT_INGRESS_BREAKER_CLEARED,
            EVENT_INGRESS_BREAKER_TRIPPED,
            EVENT_INGRESS_DROPPED_BREAKER,
        )

        if self.transition in (TRANSITION_TRIPPED, TRANSITION_ESCALATED):
            return EVENT_INGRESS_BREAKER_TRIPPED
        if self.transition in (TRANSITION_PROBE, TRANSITION_RECOVERED):
            return EVENT_INGRESS_BREAKER_CLEARED
        # Deliberately NOT folded together with "should the caller drop
        # this message" — the native gate returns ``verdict.admit`` and the
        # managed one still answers with a receipt, whether or not a row
        # was written. One function deciding both would bury that.
        if not self.admit:
            return EVENT_INGRESS_DROPPED_BREAKER
        return None

    def audit_details(self) -> Dict[str, Any]:
        """The ``details`` payload for a ``channel_trigger_audit`` row."""
        return {
            "session_key": self.session_key,
            "tier": self.tier,
            "reason": self.reason,
            "transition": self.transition,
            "window_count": self.window_count,
            "dup_ratio": round(self.dup_ratio, 3),
            "cooldown_seconds": round(self.cooldown_seconds, 1),
            "cooldown_remaining_seconds": round(
                self.cooldown_remaining_seconds, 1
            ),
            "suppressed": self.suppressed,
            "is_agent_peer": self.is_agent_peer,
        }


@dataclass
class _SessionState:
    """In-memory half of one session key's state.

    The sliding window lives here and ONLY here. Persisting a row per
    inbound message would be write amplification for data that expires in
    ten minutes; what goes to the DB is the tier transition, which happens
    a handful of times in a session's life.
    """

    events: Deque[Tuple[datetime, str]] = field(default_factory=deque)
    tier: int = 0
    cooldown_until: Optional[datetime] = None
    suppressed: int = 0
    # Start of the current clean streak — the anchor for tier decay while
    # the session KEEPS TALKING.
    clean_since: Optional[datetime] = None
    # When ``tier`` last moved, in memory. The mirror of the durable
    # ``tier_changed_at`` column, kept so a session that has gone silent
    # can be aged down without a reload: a tier > 0 session is pinned in
    # ``_sessions`` by ``prune_idle``, so it never gets a second ``_load``.
    tier_changed_at: Optional[datetime] = None
    loaded: bool = False
    # The fingerprints that were in the window when this session last
    # tripped. A half-open probe carrying one of these is the same recital
    # resuming, and must re-trip on the spot rather than re-earning a whole
    # rate_bar's worth of pipeline runs first. Bounded — see _trip.
    trip_fingerprints: set = field(default_factory=set)


def _as_aware(
    value: Optional[datetime], reference: datetime
) -> Optional[datetime]:
    """Normalise a possibly-naive timestamp against ``reference``'s zone.

    SQLite hands these back as bare strings parsed into naive datetimes
    while MySQL keeps the offset, so every read has to be normalised before
    it is compared. Doing it in one place matters here specifically: the
    comparison below sits in ``_load`` OUTSIDE its try/except (which only
    wraps the repository read), so a ``TypeError`` from mixing naive and
    aware would escape ``admit()`` and turn fail-open into fail-closed.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=reference.tzinfo)
    return value


def _fold_isolation_end(
    anchor: Optional[datetime],
    isolation_end: Optional[datetime],
    reference: datetime,
) -> Optional[datetime]:
    """The later of a tier's last move and the end of its isolation.

    One definition, three callers: ``_load`` (folding a row's already
    elapsed cooldown into the in-memory anchor), ``_half_open`` (folding
    before it destroys ``cooldown_until``), and ``_steps_of_silence``
    (folding before it measures).

    ``state.cooldown_until`` only ever holds a cooldown still in the
    future, so an elapsed one has to be folded into the anchor or its
    record is lost — that is the reason this rule has to be applied at
    read time and not only at measurement time.
    """
    anchor = _as_aware(anchor, reference)
    end = _as_aware(isolation_end, reference)
    if anchor is None:
        return end
    if end is not None and end > anchor:
        return end
    return anchor


class IngressGuard:
    """Per-conversation ingress breaker, shared by every channel.

    One instance per trigger (and one for the managed-mode coordinator).
    Not a Module — modules stay independent (binding rule #3); this is a
    ``channel/`` facility alongside ``ChannelDedupStore`` and
    ``ChannelDebounceMerger``.
    """

    def __init__(
        self,
        repo: Any = None,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        rate_threshold: int = DEFAULT_RATE_THRESHOLD,
        dup_ratio_threshold: float = DEFAULT_DUP_RATIO_THRESHOLD,
        agent_rate_threshold: int = DEFAULT_AGENT_RATE_THRESHOLD,
        agent_dup_ratio_threshold: float = DEFAULT_AGENT_DUP_RATIO_THRESHOLD,
        schedule_seconds: Tuple[float, ...] = DEFAULT_SCHEDULE_SECONDS,
        recovery_windows: int = DEFAULT_RECOVERY_WINDOWS,
    ):
        self._repo = repo
        self._window = window_seconds
        self._rate = rate_threshold
        self._dup = dup_ratio_threshold
        self._agent_rate = agent_rate_threshold
        self._agent_dup = agent_dup_ratio_threshold
        self._schedule = schedule_seconds or DEFAULT_SCHEDULE_SECONDS
        self._recovery_windows = max(1, recovery_windows)
        self._sessions: Dict[str, _SessionState] = {}
        self._admits_since_prune = 0

    # ── Public surface ────────────────────────────────────────────────

    async def admit(
        self,
        *,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        fingerprint: str,
        is_agent_peer: bool = False,
        now: Optional[datetime] = None,
    ) -> IngressVerdict:
        """Decide whether one inbound message may enter the pipeline.

        Args:
            agent_id: the receiving agent (audit + owner alerting).
            channel: ``ChannelTriggerBase.channel_name``.
            chat_id / sender_id: the conversation and who spoke.
            fingerprint: from ``content_fingerprint``; "" means "no
                content to fingerprint" and is treated as unique.
            is_agent_peer: the far side is another agent — tightens both
                thresholds.
            now: injectable clock. Callers omit it.

        Returns:
            An ``IngressVerdict``. ``admit=False`` means the caller must
            drop the message AND write an audit row — never a silent
            return (lessons #3/#5).
        """
        now = now or utc_now()
        self._admits_since_prune += 1
        if self._admits_since_prune >= _PRUNE_EVERY_ADMITS:
            self._admits_since_prune = 0
            self.prune_idle(now)
        key = session_key(agent_id, channel, chat_id, sender_id)
        state = self._sessions.get(key)
        if state is None:
            state = _SessionState()
            self._sessions[key] = state
        if not state.loaded:
            await self._load(key, state, now)

        # 1. Still cooling? Drop, count, and say so.
        if state.cooldown_until is not None:
            if now < state.cooldown_until:
                state.suppressed += 1
                return IngressVerdict(
                    admit=False,
                    session_key=key,
                    tier=state.tier,
                    reason="cooling",
                    suppressed=state.suppressed,
                    is_agent_peer=is_agent_peer,
                    cooldown_remaining_seconds=(
                        state.cooldown_until - now
                    ).total_seconds(),
                )
            return await self._half_open(
                key, state, now, agent_id, channel, chat_id, sender_id,
                fingerprint, is_agent_peer,
            )

        # 2. Record and evaluate the window.
        self._record(state, now, fingerprint)
        count, ratio = self._window_stats(state)
        rate_bar = self._agent_rate if is_agent_peer else self._rate
        dup_bar = self._agent_dup if is_agent_peer else self._dup

        if count >= rate_bar and ratio >= dup_bar:
            return await self._trip(
                key, state, now, agent_id, channel, chat_id, sender_id,
                count, ratio, is_agent_peer,
            )

        # 3. Behaving. Walk the escalation memory back down if it has been
        #    behaving long enough.
        transition = await self._maybe_recover(
            key, state, now, agent_id, channel, chat_id, sender_id
        )
        return IngressVerdict(
            admit=True,
            session_key=key,
            tier=state.tier,
            reason="recovered" if transition else "ok",
            transition=transition,
            window_count=count,
            dup_ratio=ratio,
            is_agent_peer=is_agent_peer,
        )

    def open_session_count(self) -> int:
        """Sessions currently carrying escalation memory.

        Isolation is a STANDING state, not just the row written at trip
        time (incident lesson #4) — this feeds ``health_snapshot()`` and
        the heartbeat so a zombie loop cannot burn 70 hours behind an
        all-green dashboard again.
        """
        return sum(1 for s in self._sessions.values() if s.tier > 0)

    def cooling_session_count(self, now: Optional[datetime] = None) -> int:
        """Sessions whose ingress is suppressed right now.

        Takes the same injectable clock as ``admit`` — a count that reads
        the wall clock while the state under it was built on a synthetic
        one cannot be asserted on, and "is this conversation cooling?" has
        exactly one right answer per instant.
        """
        now = now or utc_now()
        return sum(
            1
            for s in self._sessions.values()
            if s.cooldown_until is not None and now < s.cooldown_until
        )

    async def warm_start(
        self, channel: str, now: Optional[datetime] = None
    ) -> int:
        """Load this channel's still-isolated sessions back into memory.

        Two things were broken without it, both of which undercut the
        reason the durable table exists:

        1. ``health_snapshot`` and the heartbeat read the IN-MEMORY counts.
           After a restart ``_sessions`` is empty, so a process holding 50
           isolated conversations — 10 of them mid-24h-cooldown — reported
           ``ingress_breaker_open_count: 0``. Deploy, and the dashboard
           goes green while the conversations stay deaf. That is precisely
           the L2-health blind spot (incident lesson #4) this design cites.
        2. A reloaded cooldown only revived when that key next spoke. The
           row was right; nothing read it until the damage was already
           being re-done.

        Loads only the sessions still INSIDE a cooldown.

        The first version loaded every ``tier > 0`` row and justified it
        with "closed ones are swept by retention" — which has the relation
        backwards: retention sweeps ``tier = 0`` EXCLUSIVELY, and this
        loads ``tier > 0`` exclusively, so the two never intersect. A
        session that trips once and goes quiet keeps its row forever (tier
        decay needs further ``admit()`` calls it will never get), so
        "every open row" is a lifetime trip log, not a current state.
        Loading it would make both the footprint and
        ``open_session_count()`` climb with every deploy — turning I4's
        "reports 0 after a restart" into "reports a growing historical
        total", which is just as useless for "how many are isolated now".

        The escalation memory does NOT need preloading: ``_load()`` fetches
        it lazily at the only moment it matters — when that key speaks
        again. What preloading buys is the observability surface, and that
        surface only ever meant "currently suppressed".

        Returns the number of sessions restored.
        """
        if self._repo is None:
            return 0
        now = now or utc_now()
        try:
            rows = await self._repo.find_open(
                channel, cooling_only=True, now=now
            )
        except Exception as e:  # noqa: BLE001 — never block startup
            logger.warning(
                f"IngressGuard: warm start failed for {channel} "
                f"({type(e).__name__}: {e}) — counts will fill in lazily"
            )
            return 0

        restored = 0
        for row in rows:
            if not row.session_key or row.session_key in self._sessions:
                continue
            state = _SessionState()
            state.loaded = True
            state.tier = row.tier or 0
            # Carried so the sweep can age these down once their cooldown
            # elapses. Without it they are pinned at tier > 0 for the life
            # of the process and ``open_session_count()`` drifts from
            # "isolated now" back toward "tripped at some point" — the
            # distortion this method's `cooling_only` load exists to avoid.
            # No fold here, unlike ``_load``: this method loads with
            # ``cooling_only=True``, so every row it sees still has its
            # cooldown in the future and it lands in
            # ``state.cooldown_until`` below — where ``_steps_of_silence``
            # folds it anyway. Folding twice would be a second copy of the
            # rule that no test could tell apart from the first.
            state.tier_changed_at = _as_aware(row.tier_changed_at, now)
            # NOT seeded from ``row.suppressed_count`` — see _load().
            state.suppressed = 0
            cooldown_until = _as_aware(row.cooldown_until, now)
            if cooldown_until is not None and now < cooldown_until:
                state.cooldown_until = cooldown_until
            self._sessions[row.session_key] = state
            restored += 1
        if restored:
            logger.info(
                f"IngressGuard[{channel}]: restored {restored} cooling "
                f"sessions from the durable store"
            )
        return restored

    def prune_idle(self, now: Optional[datetime] = None) -> int:
        """Drop in-memory state for conversations that have gone quiet.

        A session is droppable only when it is carrying NOTHING worth
        keeping: closed (``tier`` 0), not cooling, and with no events left
        inside the window. Anything else — an active window, a live
        cooldown, or escalation memory — is exactly what we promised to
        remember, so it stays.

        Dropping a closed session is lossless by construction: its durable
        row (if it ever had one) stays in the DB and is re-read lazily the
        next time that key speaks.

        Also ages silent tiers down first (in memory only), so a session
        whose escalation memory has expired becomes droppable on the same
        pass. That is a real change of state, not just reclamation — a
        caller that reschedules this method changes when tiers decay, even
        though the arithmetic is unaffected. The durable row is corrected
        the next time that key speaks.

        "Droppable" means not cooling RIGHT NOW, not "never cooled":
        ``cooldown_until`` is only ever cleared by a probe, so a criterion
        of "never had one" would pin every session that has ever tripped.

        Two visible consequences of dropping:

        1. The next message from that key reloads the row, and ``_load``
           keeps only a cooldown still in the future — so an elapsed one is
           not restored and the message takes the ordinary counting path
           instead of arriving as a half-open probe. Same shape as the
           post-restart behaviour ``_half_open`` already documents (an
           empty ``trip_fingerprints`` falls back to re-earning the
           window); the durable tier is unaffected, which is the half that
           matters.
        2. ``state.suppressed`` goes with it, and that number has exactly
           one way into the DB — the probe. A session dropped before its
           probe therefore leaves ``suppressed_count`` at whatever the last
           transition wrote (0 for a first trip), so "how much did this
           isolation absorb?" is unanswerable for the very shape where the
           breaker WORKED: stormed, isolated, went quiet, came back later.
           Not worth fixing by writing on the way out — that would make
           this method async, and ``admit()`` calls it without awaiting.

        Returns the number of sessions dropped — decayed-but-still-held
        sessions are not counted.
        """
        now = now or utc_now()
        # Age silent sessions down FIRST, so the ones that reach tier 0
        # become droppable in this same pass. Without it a tier > 0 session
        # is pinned here forever: it is deliberately never dropped, so it
        # never gets a second ``_load``, and ``_maybe_recover`` only runs
        # for sessions that keep talking. ``open_session_count()`` — the L2
        # metric this design offers against the 8/14 blind spot — would
        # then drift from "isolated right now" toward "tripped at any point
        # since the last deploy", in a process built to run for days
        # (binding rule #14). That is the same distortion ``warm_start``
        # takes pains to avoid, growing back from the other end.
        #
        # Memory-side only, because this method is sync and ``admit()``
        # calls it without awaiting. The durable row is corrected by
        # ``_decay_for_silence`` the next time that key speaks: the row is
        # still tier > 0 with the same anchor, so it computes the same
        # answer and writes ``aged_out``.
        self._decay_silent_in_memory(now)
        cutoff = now - timedelta(seconds=self._window)
        stale = [
            key
            for key, s in self._sessions.items()
            if s.tier <= 0
            # "not cooling NOW", not "never had a cooldown". The field is
            # only ever cleared by a probe, so every session that tripped,
            # loaded while cooling, or came from ``warm_start`` carries a
            # non-null value forever — i.e. exactly the sessions this sweep
            # exists for would be pinned here permanently, and never get
            # the second ``_load`` that corrects their durable row. Same
            # predicate as ``cooling_session_count``.
            and (s.cooldown_until is None or s.cooldown_until <= now)
            and (not s.events or s.events[-1][0] < cutoff)
        ]
        for key in stale:
            del self._sessions[key]
        if stale:
            logger.debug(
                f"IngressGuard: pruned {len(stale)} idle sessions "
                f"({len(self._sessions)} retained)"
            )
        return len(stale)

    def forget_agent(self, agent_id: str) -> int:
        """Drop every in-memory session belonging to one agent.

        Called from ``ChannelTriggerBase._stop_subscriber`` when an
        agent's subscriber stops (credential unbound, or shutdown). It sat
        here with no caller for one release, and the docstring said so in
        the future tense on purpose: a "Called when ..." on a method nobody
        calls reads as "the unbind path is already handled", and it was
        not. ``test_ingress_guard_all_paths`` now pins that it has one.

        ``prune_idle`` cannot cover this: it deliberately keeps
        sessions carrying escalation memory, so an unbound agent's tripped
        conversations would sit in memory for the life of the process.

        Deliberately does NOT delete the durable rows: memory is a cache of
        the DB here, not the other way round, and a re-bind must not hand a
        re-offender a fresh budget.
        """
        prefix = f"{agent_id}|"
        stale = [k for k in self._sessions if k.startswith(prefix)]
        for key in stale:
            del self._sessions[key]
        return len(stale)

    # ── Internals ─────────────────────────────────────────────────────

    async def _load(
        self, key: str, state: _SessionState, now: datetime
    ) -> None:
        """Lazily pull the durable row the first time we see a key.

        One read per session key per process lifetime — plus, for a row
        that ages out on the way in, one write. The hot path never touches
        the DB again.
        """
        state.loaded = True
        if self._repo is None:
            return
        try:
            row = await self._repo.get(key)
        except Exception as e:  # noqa: BLE001
            # Fail open: the guard is not an authorization gate.
            logger.warning(
                f"IngressGuard: durable load failed for {key} "
                f"({type(e).__name__}: {e}) — continuing memory-only"
            )
            return
        if row is None:
            return
        state.tier = row.tier or 0
        # Carried into memory, not merely read for the decay below: without
        # it a lazily-loaded session has no in-memory anchor, so
        # ``prune_idle``'s sweep skips it forever and only sessions that
        # tripped inside THIS process can ever be aged down — half of the
        # gap that sweep was added to close.
        # Folded with the row's cooldown end even when that has already
        # elapsed. ``state.cooldown_until`` below deliberately keeps only a
        # FUTURE cooldown, so without this the in-memory anchor would drop
        # back to trip time and the next sweep would undo, milliseconds
        # later, the decay this same load just declined to apply.
        state.tier_changed_at = _fold_isolation_end(
            row.tier_changed_at, row.cooldown_until, now
        )
        # NOT seeded from ``row.suppressed_count``: since M9 that column
        # records what the PREVIOUS isolation absorbed, so using it here
        # would start the NEXT isolation's counter part-way up and make
        # the figure accumulate across rounds — inflating exactly the
        # number the escalation notice puts in the headline. After a
        # reload we honestly do not know what was dropped before, so we
        # count from zero; the durable column keeps the finished number
        # for SQL.
        state.suppressed = 0
        cooldown_until = _as_aware(row.cooldown_until, now)
        if cooldown_until is not None and now < cooldown_until:
            state.cooldown_until = cooldown_until
        await self._decay_for_silence(key, state, row, now)


    async def _decay_for_silence(
        self,
        key: str,
        state: _SessionState,
        row: ChannelIngressBreaker,
        now: datetime,
    ) -> None:
        """Age the tier down for time spent silent, on lazy load.

        ``_maybe_recover`` only runs from ``admit()``, so it decays a tier
        solely while the session KEEPS TALKING. A session that tripped and
        then went quiet keeps its tier forever: nothing polls, and the
        durable row is read back verbatim on the next load. That is the
        exact failure ``_maybe_recover``'s own docstring says it exists to
        prevent — a conversation that misbehaved once carrying a 24h-capable
        tier for the rest of its life — implemented for only half the cases.

        It is also why the table never shrinks: ``cleanup_older_than_days``
        deliberately sweeps only ``tier = 0`` rows, so a session stuck at
        tier > 0 is a row that can never be reclaimed.

        Anchored on ``tier_changed_at``, which exists for this and only
        this. The two neighbouring columns both look like they would do and
        both are wrong: ``last_tripped_at`` stands still while
        ``_maybe_recover`` walks the tier down, so it would re-credit
        silence already paid out and decay too far; ``updated_at`` is
        stamped by the repository off the wall clock while this state
        machine runs on a caller-supplied ``now``, so the two disagree
        wherever the caller's clock is not wall time.
        """
        if state.tier <= 0:
            return
        steps = self._steps_of_silence(row.tier_changed_at, row.cooldown_until, now)
        if steps <= 0:
            return
        before = state.tier
        state.tier = max(0, state.tier - steps)
        # Credit the silence that has already elapsed rather than leaving
        # this None, or the first message back would start a fresh window
        # before any further step could be earned.
        state.clean_since = now
        state.tier_changed_at = now
        logger.info(
            f"IngressGuard aged down on load: {key} "
            f"tier={before} -> {state.tier} (silent {steps} steps)"
        )
        if state.tier == 0:
            # Written back so the row becomes sweepable. Intermediate steps
            # are not persisted: the anchor is unchanged, so a restart
            # recomputes the same number — but a row stuck above zero is one
            # ``cleanup_older_than_days`` can never take.
            await self._persist(
                key,
                {
                    "channel": row.channel or "",
                    "agent_id": row.agent_id or "",
                    "chat_id": row.chat_id or "",
                    "sender_id": row.sender_id or "",
                    "tier": 0,
                    "cooldown_until": None,
                    "last_reason": "aged_out",
                    "tier_changed_at": now,
                },
            )

    def _steps_of_silence(
        self,
        anchor: Optional[datetime],
        isolation_end: Optional[datetime],
        now: datetime,
    ) -> int:
        """How many decay steps of SILENCE this session has earned.

        One definition, two callers — the durable load path and the
        in-memory sweep. They previously carried a copy each, reading
        ``row.*`` and ``state.*`` respectively, and the copies drifted:
        the same sentence about "the later of the two" landed on two
        different facts because the two sources hold different values for
        ``cooldown_until``. Keeping the rule in one place is what makes
        "they agree" a property of the code rather than of a comment.

        Silence starts when the isolation ENDS. ``tier_changed_at`` is
        stamped at trip time and the cooldown runs from there, so counting
        from it bills the sentence itself as good behaviour — the same
        double-credit that ruled out ``last_tripped_at``. At tier 3 the
        cooldown alone is six steps.

        This is ALSO the whole of "never decay while still isolated": an
        unexpired cooldown puts the anchor in the future, so the result can
        only be zero. A separate "am I cooling right now?" check would read
        as a second, independent criterion and invite the two to drift.

        Returns 0 — never raises — when the step length is zero. That is
        the only division in this class, and it sits under ``admit()``:
        letting a ZeroDivisionError out would turn fail-open into
        fail-closed and drop every message on the channel (binding
        rule #16).

        A missing ``anchor`` is not a short circuit: the fold runs first,
        so a row that records only an isolation end still measures from it.
        Only when BOTH are absent is there no origin to measure from.
        """
        # Before the fold, and not inside it: the step length is this
        # class's business, not the anchor rule's.
        if self._decay_step_seconds <= 0:
            return 0
        anchor = _fold_isolation_end(anchor, isolation_end, now)
        if anchor is None:
            # Neither a tier move nor an isolation end is recorded. Decaying
            # from an unknown origin would be a guess.
            return 0
        steps = int((now - anchor).total_seconds() // self._decay_step_seconds)
        return max(0, steps)

    def _decay_silent_in_memory(self, now: datetime) -> None:
        """The sweep half of the decay. Shares ``_steps_of_silence`` with
        the durable path, so the two cannot disagree about what counts as
        silence — they did, once, and that is why the rule moved out.

        Memory only: ``prune_idle`` is sync and ``admit()`` does not await
        it. The durable row is corrected by ``_decay_for_silence`` the next
        time that key speaks.
        """
        for key, state in self._sessions.items():
            if state.tier <= 0:
                continue
            steps = self._steps_of_silence(
                state.tier_changed_at, state.cooldown_until, now
            )
            if steps <= 0:
                continue
            before = state.tier
            state.tier = max(0, state.tier - steps)
            state.tier_changed_at = now
            state.clean_since = now
            logger.info(
                f"IngressGuard aged down while silent: {key} "
                f"tier={before} -> {state.tier}"
            )

    def _record(self, state: _SessionState, now: datetime, fingerprint: str) -> None:
        state.events.append((now, fingerprint))
        cutoff = now - timedelta(seconds=self._window)
        while state.events and state.events[0][0] < cutoff:
            state.events.popleft()
        while len(state.events) > _MAX_EVENTS_PER_SESSION:
            state.events.popleft()

    def _window_stats(self, state: _SessionState) -> Tuple[int, float]:
        """(message count, duplicate ratio) over the live window.

        ratio = 1 - distinct/count, so 30 identical messages score ~0.97
        and 30 different ones score 0.0. Empty fingerprints each count as
        their own distinct value — see ``content_fingerprint``.
        """
        count = len(state.events)
        if count == 0:
            return 0, 0.0
        distinct = 0
        seen: set[str] = set()
        for _, fp in state.events:
            if not fp:
                distinct += 1  # unfingerprintable → never a duplicate
                continue
            if fp not in seen:
                seen.add(fp)
                distinct += 1
        return count, 1.0 - (distinct / count)

    async def _trip(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        count: int,
        ratio: float,
        is_agent_peer: bool,
        reason_hint: str = "",
    ) -> IngressVerdict:
        was_open = state.tier > 0
        # Snapshot what this session was saying when it tripped, so the
        # half-open probe can recognise the recital resuming. Capped: a
        # storm is by definition low-cardinality, and an unbounded set here
        # would be one more per-session leak.
        if state.events:
            state.trip_fingerprints = {
                fp for _, fp in state.events if fp
            }
            if len(state.trip_fingerprints) > _MAX_TRIP_FINGERPRINTS:
                state.trip_fingerprints = set(
                    list(state.trip_fingerprints)[:_MAX_TRIP_FINGERPRINTS]
                )
        state.tier += 1
        state.tier_changed_at = now
        cooldown = self._schedule[min(state.tier - 1, len(self._schedule) - 1)]
        state.cooldown_until = now + timedelta(seconds=cooldown)
        suppressed_before = state.suppressed
        # A fresh isolation absorbs a fresh count, so the number in the
        # alert answers "how much did THIS cooldown swallow?".
        state.suppressed = 0
        state.clean_since = None
        state.events.clear()

        transition = TRANSITION_ESCALATED if was_open else TRANSITION_TRIPPED
        reason = reason_hint or (
            "agent_peer_repeat_storm" if is_agent_peer else "repeat_storm"
        )
        logger.error(
            f"IngressGuard {transition}: {key} tier={state.tier} "
            f"count={count} dup_ratio={ratio:.2f} agent_peer={is_agent_peer} "
            f"cooldown={cooldown}s — ingress suppressed for this conversation"
        )
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "tier": state.tier,
                "cooldown_until": state.cooldown_until,
                # What the isolation that just ENDED absorbed. On a first
                # trip that is 0; on an escalation from a half-open probe
                # it is the real count, and that is the only path where a
                # non-zero value is ever produced — writing 0 here left the
                # column dead for exactly the scenario it was added for.
                # (``state.suppressed`` is separately zeroed above, for the
                # new isolation.)
                "suppressed_count": suppressed_before,
                "last_reason": reason,
                "last_tripped_at": now,
                "tier_changed_at": now,
            },
        )
        return IngressVerdict(
            admit=False,
            session_key=key,
            tier=state.tier,
            reason=reason,
            transition=transition,
            window_count=count,
            dup_ratio=ratio,
            cooldown_seconds=cooldown,
            # The trip is the start of the isolation, so the whole of it
            # is still ahead.
            cooldown_remaining_seconds=cooldown,
            suppressed=suppressed_before,
            is_agent_peer=is_agent_peer,
        )

    async def _half_open(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
        fingerprint: str,
        is_agent_peer: bool,
    ) -> IngressVerdict:
        """Cooldown elapsed: probe with exactly one message.

        The tier is KEPT — same reasoning as ``_breaker_release`` on the
        credential breaker. A session that clears its cooldown and
        immediately resumes reciting must land on the next step of the
        schedule, not restart at five minutes; otherwise a persistent
        loop oscillates at the cheapest tier forever.

        **The probe is judged, not just counted.** The first version
        cleared the window and returned, which meant re-tripping required
        re-earning a whole ``rate_bar`` — so every cooldown actually let
        20 (agent) or 60 (human) full pipeline runs through, each
        producing another outbound message. "Exactly one probe" was true
        of the admission and false of the consequence. If the probe
        carries a fingerprint this session was reciting when it tripped,
        that IS the loop resuming and it re-trips immediately.

        A genuinely new message does not re-trip: it clears the memory and
        resumes ordinary counting, so a person whose first sentence after
        a 24h cooldown is real conversation is not punished for it.

        After a restart ``trip_fingerprints`` is empty (memory-only), so
        the first probe of a reloaded session falls back to the
        re-earn-the-window behaviour. Acceptable: the durable half already
        held the cooldown, which is the part that mattered.
        """
        suppressed = state.suppressed
        # Fold the isolation's END into the decay anchor BEFORE destroying
        # it. ``cooldown_until`` is the only record of when the sentence
        # finished, and this line is where it stops existing — after which
        # ``_steps_of_silence`` would fall back to ``tier_changed_at``
        # (trip time) and count the whole cooldown as silence. At tier 3
        # that is six free steps, so the very next sweep would zero the
        # escalation memory a few milliseconds after this probe
        # deliberately kept it. A far side that storms, waits out the
        # cooldown, sends one new message and storms again would then sit
        # on the cheapest tier forever — exactly what this method's
        # docstring says it prevents.
        #
        # The END, not ``now``: the durable path reads the row's
        # ``cooldown_until``, so anchoring on anything else would give the
        # two paths different answers again. It also keeps the genuinely
        # silent gap between "cooldown elapsed" and "probe arrived".
        state.tier_changed_at = _fold_isolation_end(
            state.tier_changed_at, state.cooldown_until, now
        )
        state.cooldown_until = None

        if fingerprint and fingerprint in state.trip_fingerprints:
            # NOTE: do not zero ``state.suppressed`` before this call —
            # ``_trip`` reads it as the count the ENDING isolation absorbed
            # and reports it on the verdict (then zeroes it for the new
            # one). Resetting here first silently dropped that number, so
            # an escalation's audit row claimed it had absorbed nothing.
            state.events.clear()
            return await self._trip(
                key, state, now, agent_id, channel, chat_id, sender_id,
                count=1, ratio=1.0, is_agent_peer=is_agent_peer,
                reason_hint="probe_repeated",
            )

        state.suppressed = 0
        state.events.clear()
        state.trip_fingerprints = set()
        state.clean_since = now

        logger.info(
            f"IngressGuard probe: {key} tier={state.tier} "
            f"suppressed={suppressed} — admitting one message to re-test"
        )
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "cooldown_until": None,
                # tier and its anchor go in the SAME write, as they do in
                # ``_trip`` and ``_maybe_recover``. They are two halves of
                # one fact ("what the tier is" / "when it last moved"), and
                # the in-memory sweep is the one path that moves tier
                # without persisting — its whole justification is that the
                # row still says tier N with an untouched anchor, so the
                # next ``_load`` recomputes the same answer. Writing only
                # the new anchor here would hand that reload an old tier
                # with a fresh anchor: the served sentence grows back, and
                # a session that had correctly decayed to 0 quietly picks
                # its tier up again on the first message after a cooldown.
                "tier": state.tier,
                "tier_changed_at": state.tier_changed_at,
                # The isolation that just ended absorbed this many messages.
                # This is the ONLY moment the number can be persisted: the
                # hot path deliberately never writes, so if we wrote 0 here
                # (as the first version did) the column would hold 0 for
                # its entire life and the "how much did this cooldown
                # swallow?" question it exists to answer would be
                # unanswerable from SQL.
                "suppressed_count": suppressed,
                "last_reason": "cooldown_expired",
            },
        )
        return IngressVerdict(
            admit=True,
            session_key=key,
            tier=state.tier,
            reason="cooldown_expired",
            transition=TRANSITION_PROBE,
            suppressed=suppressed,
            is_agent_peer=is_agent_peer,
        )

    @property
    def _decay_step_seconds(self) -> float:
        """How long one tier step takes to decay. One definition, two
        callers — ``_maybe_recover`` (the session kept talking) and
        ``_steps_of_silence`` (it went silent; shared by the lazy-load path
        and the in-memory sweep). Two copies of a decay rate is two rates
        the day someone tunes one of them."""
        return self._window * self._recovery_windows

    async def _maybe_recover(
        self,
        key: str,
        state: _SessionState,
        now: datetime,
        agent_id: str,
        channel: str,
        chat_id: str,
        sender_id: str,
    ) -> Optional[str]:
        """Walk the tier down one step per N clean windows, to zero.

        The design's "逐级降回，最终清零". Without it a conversation that
        misbehaved once in its life would carry a 24h-capable tier
        forever, and the first bad minute a year later would cost a day.
        """
        if state.tier <= 0:
            return None
        if state.clean_since is None:
            state.clean_since = now
            return None
        if (now - state.clean_since).total_seconds() < self._decay_step_seconds:
            return None

        state.tier -= 1
        state.clean_since = now
        state.tier_changed_at = now
        logger.info(f"IngressGuard recovered: {key} tier={state.tier}")
        await self._persist(
            key,
            {
                "channel": channel,
                "agent_id": agent_id,
                "chat_id": chat_id,
                "sender_id": sender_id,
                "tier": state.tier,
                "cooldown_until": None,
                "last_reason": "recovered",
                "tier_changed_at": now,
            },
        )
        return TRANSITION_RECOVERED

    async def _persist(self, key: str, updates: Dict[str, Any]) -> None:
        """Write-through on transition only. Never raises: losing the
        durable copy costs restart-survival for one session; taking the
        ingress path down would be strictly worse."""
        if self._repo is None:
            return
        try:
            await self._repo.upsert_state(key, updates)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"IngressGuard: durable write failed for {key} "
                f"({type(e).__name__}: {e}) — in-memory state still enforced"
            )
