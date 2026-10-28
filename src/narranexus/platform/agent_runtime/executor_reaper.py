"""
@file_name: executor_reaper.py
@author:
@date: 2026-06-17
@description: Idle-cull coordinator for per-user Executor containers.

Pure coordinator (dependency-injected): it owns neither the concurrency
state nor the docker transport. It periodically asks the admission
controller which users have gone idle past the TTL, and asks a ``stop_fn``
(the broker client) to stop them. This keeps the concerns separate:
  - AgentAdmissionController — concurrency + idle bookkeeping
  - ExecutorReaper          — WHEN to cull (this file)
  - run_recorder.first_live_run_id — IS the user actually idle (DB truth)
  - broker_client.stop_executor — HOW to stop (docker transport)

Binding rule #14: only idle executors are ever reaped — a running loop is
never interrupted. The cull just delays the next start by a cold boot,
surfaced to the user via the "waking up" UX.

That guarantee needs the third collaborator, and used to be stated
without it. The admission controller is a PER-PROCESS singleton while
executor containers are shared per USER, so "zero active loops" only ever
meant "zero in the process asking". The cloud orchestrator runs as
backend + workers and this reaper only runs in backend, so every run
driven by workers (group chat / message bus, scheduled jobs, channel
triggers) was invisible to it — and on 2026-07-31 it stopped the
container out from under a live group-chat reply, surfacing to the user
as ``infra_transient``. The idle claim is therefore vetoed by a
cross-process liveness check against the ``events`` table, which every
process writes to (incident lesson #5).

That check has a second consumer, which is why it is a module-level
function here rather than a private of the reaper: the broker's stale-image
replacement destroys the same container for a different reason, and asking
the same question two ways is how the two answers drift apart. See
``no_live_recorded_run_for``.
"""
from __future__ import annotations

import asyncio
import functools
import os
from time import monotonic
from typing import Awaitable, Callable, Optional

from loguru import logger

from narranexus.platform.agent_runtime.admission import (
    AgentAdmissionController,
    BusyCheck,
    get_admission_controller,
)
from narranexus.platform.agent_runtime.run_recorder import RECORDING_DISABLED_ENV
from narranexus.platform.schema.executor_audit import (
    EVENT_CULL_DISABLED,
    EVENT_CULL_SKIPPED_BUSY,
)

# Returns whether the executor actually stopped. False means the broker
# REFUSED because the container reported work in flight — a normal outcome,
# not an error, and one the reaper must be able to tell from success.
#
# Consumed as `if not await stop_fn(...)`, so a falsy return of any kind means
# "not stopped". That polarity is the safe one: a future branch that forgets
# to return leaves the user its idle stamp and retries next pass, whereas
# treating silence as success drops the stamp and leaves a container that was
# never stopped never reconsidered either (see reap_once).
StopFn = Callable[[str], Awaitable[bool]]

DEFAULT_IDLE_TTL_SEC = 1200   # 20 min (locked decision)
DEFAULT_INTERVAL_SEC = 120

# Stand-in run id for "we could not find out". Not a real run, but it gets a
# real run's answer — hands off. Never written to a cull_skipped_busy row:
# that metric counts runs actually saved, and "unknown" in the run_id column
# would make it unreadable. The blind case has its own pass-level reporting
# (see _report_pass).
UNKNOWN_RUN = "unknown"

# Callers already warned that run recording is off. Bounded (two callers),
# and reset when the switch goes back on so a second pull warns again.
_recording_off_warned: set[str] = set()

# Budget for ONE liveness lookup. Separate from the admission layer's
# whole-batch budget, and the important one: a timeout here is answered
# "busy" AND counted as blind, so the pass-level alarm below trips. Without
# it, a DB that is SLOW rather than dead cancels the lookup mid-await, the
# tally never runs, and a pass in which nothing could be judged reports the
# same zeros as a healthy pass with nothing to do.
#
# It must stay below admission._VETO_BUDGET_S, or the outer budget cancels
# the lookup first and the accounting above is bypassed. Rather than trust a
# ratio between two constants in two modules, the reaper HANDS this value to
# claim_idle_users, which then refuses to start a check the batch cannot see
# through — so "started ⇒ counted" holds structurally, whatever the two
# numbers are. (Picking a value that does not divide the batch budget was
# considered and dropped: it moves which candidate straddles the boundary
# without removing it, and in real timings the outer budget wins that race
# every pass, not occasionally.)
#
# The tally still under-reports on a fully wedged DB — candidates held back
# for want of budget are never judged, so `judged` is a floor, not the
# candidate count. The ALARM is unaffected: everyone judged came back blind,
# so the pass reads as blind.
_PER_CANDIDATE_S = 12.0

# Budget for one audit WRITE. Its own constant, not a reuse of the lookup
# budget above: a write plus its pool acquisition has a different natural
# scale from an indexed read, and welding the two to one name means every
# future retune of one silently retunes the other. Small enough that a
# wedged pool cannot park a pass, generous next to a healthy insert.
_AUDIT_WRITE_S = 5.0

# Last pass's outcome, for the L2 read-side (see reaper_status). Module level
# because the reaper is one background task per process and the admin route
# has no handle on the instance; None until a pass completes, so a process
# that never started one says so instead of lying with zeros.
_LAST_PASS: Optional[dict] = None
_LAST_PASS_AT: Optional[float] = None      # monotonic; drives age/staleness
_TASK_ERROR: Optional[str] = None          # set by the done-callback

# Passes between repeats of the "culling is blind" warning. The first blind
# pass warns immediately; at the 120s default this then repeats hourly.
# Warning every pass would be noise nobody reads; warning once would vanish
# with the next `docker restart` (incident lesson #5) while the state persists
# in the environment.
_BLIND_WARN_EVERY = 30


def reaper_status() -> dict:
    """L2 health of the idle-cull reaper, for the admin runtime snapshot.

    Answers the question the ``cull_skipped_busy`` metric cannot: culling
    stopping entirely looks exactly like nothing needing to be culled. The
    fail-safe paths (recording kill switch on, DB unreadable) make EVERY user
    read as busy, so a blind reaper reaps zero forever and is otherwise
    indistinguishable from a healthy idle system — until executors pile up to
    the broker's cap and new users start getting refused.

    Read it in this order — the first two are L2, the counts are only
    meaningful once they pass (incident lesson #4: "the task still exists" is
    L1 and proves nothing):

    * ``stale`` — no pass completed in 3 intervals. The counts below are
      frozen at whatever the last good pass saw, so a wedged reaper otherwise
      reports healthy numbers forever.
    * ``task_error`` — the background task died. Its only other trace is one
      log line at death, which the next log rotation eats.
    * ``veto_installed`` — false means this process is culling on its local
      view alone, i.e. the 2026-07-31 configuration, and it is reported
      rather than hidden. None means no pass has reported yet — distinct from
      false on purpose, since "we do not know" and "there is no guard" call
      for opposite reactions.
    * ``blind_passes`` — consecutive passes in which nothing could be judged.
      Non-zero and climbing means nothing is reclaiming executors here.
      EDGE-cleared, not level-cleared: only a pass that judges someone
      successfully resets it, because a pass with no candidates carries no
      information either way. Read it together with ``stale`` — the counter
      standing still can mean "recovered and nobody is due" or "no pass has
      run at all", and only ``stale`` separates those.

    The key set is the SAME on every path, including before the first pass
    has completed — which is every process's first ``interval_seconds`` after
    a deploy, and forever in a process with no broker. A consumer indexing
    ``blind_passes`` must not get a KeyError in exactly the window someone is
    watching a deploy. Unknowns are None (``stale``, ``veto_installed``);
    counts that are genuinely zero are 0.

    Never raises: it is one section of an endpoint whose contract is that no
    single section can 500 it.
    """
    age = None if _LAST_PASS_AT is None else monotonic() - _LAST_PASS_AT
    stale_after = (_LAST_PASS or {}).get("interval_seconds", DEFAULT_INTERVAL_SEC) * 3
    return {
        "running": _LAST_PASS is not None,
        "age_seconds": None if age is None else round(age, 1),
        "stale": None if age is None else age > stale_after,
        "task_error": _TASK_ERROR,
        # Defaults for the pre-first-pass path. veto_installed is None, NOT
        # False: claiming "no cross-process guard" about a reaper that simply
        # has not reported yet would re-run the 2026-07-31 confusion in
        # reverse, telling the reader runs are being cut off when they are not.
        "veto_installed": None,
        "interval_seconds": None,
        "reaped": 0,
        "blind_passes": 0,
        "judged": 0,
        "vetoed": 0,
        "blind": 0,
        "recheck_judged": 0,
        "recheck_vetoed": 0,
        "refused_busy": 0,
        **(_LAST_PASS or {}),
    }


async def live_run_elsewhere(
    user_id: str,
    *,
    caller: str,
    consequence: str,
    exclude_run_id: Optional[str] = None,
) -> Optional[str]:
    """The id of a run live in ANY process for this user, or None.

    THE cross-process "is this user busy?" call for anything that stops or
    destroys a container. Reads the ``events`` table because that is the only
    place every process's runs meet.

    Never raises. Every failure path answers "busy", with ``UNKNOWN_RUN`` as
    the id so callers can tell a real blocker from an unknowable one. Not
    knowing must never authorise destroying anything (binding rule #14).

    ``caller`` and ``consequence`` only shape the logs, and both are
    REQUIRED for the same reason: the consumers suffer DIFFERENT outcomes
    when the answer is unknowable (culling stops vs. executor images stop
    rolling), and the warning fires once per caller per process — so the one
    line somebody gets has to name the right subsystem. Required rather than
    defaulted because a default is necessarily one consumer's outcome, and
    the next consumer that omits it inherits that text silently.

    A NEW consumer that omits either gets a TypeError on its first call. The
    reaper does not: it binds both here (``_REAPER_LIVENESS``), and a
    TypeError on that path is swallowed by the pass-level handlers that keep
    one user's failure from aborting a cull — it surfaces as "reap pass
    error", or as "failed to stop executor", which reads like a broker fault.
    So the reaper's binding is pinned by
    ``test_the_reaper_binds_its_own_log_subject`` instead of by this
    signature. Anyone binding these for a third consumer needs the same.
    """
    from narranexus.platform.agent_runtime.run_recorder import (
        first_live_run_id,
        recording_enabled,
    )

    if not recording_enabled():
        # The kill switch for trigger-path run recording turns off exactly
        # the runs this guard exists to protect: without a recorder their
        # events row never flips to 'running', so the DB would report them
        # idle. An observability switch must not silently become a licence to
        # destroy containers — while it is on, nothing is reapable.
        if caller not in _recording_off_warned:
            _recording_off_warned.add(caller)
            logger.warning(
                f"[{caller}] run recording is disabled ({RECORDING_DISABLED_ENV}) "
                f"— cross-process liveness is unknowable, so {consequence} "
                f"while it stays disabled."
            )
        return UNKNOWN_RUN
    _recording_off_warned.discard(caller)
    try:
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        return await first_live_run_id(db, user_id, exclude_run_id=exclude_run_id)
    except Exception as e:  # noqa: BLE001 — no verdict ⇒ assume busy
        logger.warning(f"[{caller}] busy check unavailable for user={user_id}: {e}")
        return UNKNOWN_RUN


async def no_live_recorded_run_for(
    user_id: str, *, active_run_id: Optional[str] = None
) -> bool:
    """True when this user has no live RECORDED run — the verdict callers
    hand to ``ensure_executor`` as ``allow_stale_replace``.

    Named for the evidence, not for the conclusion. An earlier name said
    "replacement is safe", which promises more than this can see: it knows
    about runs in the ``events`` table and nothing else. Anything else living
    on that container is invisible here —

      * office-watch proxy sessions (``backend/routes/office_watch/proxy.py``)
        run ``officecli watch`` INSIDE the container and stream from it; a
        replacement takes the watch process and its port with it
      * anything future that holds the container without recording a run

    and the question a caller turns this into is about the CONTAINER, not
    about itself: "nothing else of MINE is on it" does not exclude another
    subsystem's session. No caller can establish that today, so every caller
    that passes this is accepting a residual risk. It is currently acceptable
    only because the idle cull already destroys such a container on its TTL;
    the fix is to give non-run holders a lease row that
    ``live_run_elsewhere`` reads, which removes the risk for every consumer
    at once instead of per caller.

    Second consumer of the liveness answer above, and it lives here so there
    is ONE place that asks "is anyone using this container?". The alternative
    is a second running-plus-heartbeat query elsewhere, whose staleness rule
    drifts from this one the first time either is touched.

    The broker cannot answer this itself and should not learn how: it is the
    one component with docker access, and its threat model rests on having
    exactly one caller-controlled input (a user_id it validates). Handing it
    DB credentials to look up run state would widen that surface for a fact
    the orchestrator already holds.

    ``active_run_id`` is the asking run's own id, excluded from the answer:
    at ensure() time the caller's events row is already ``running`` but it
    has not connected to the container yet, so counting itself would mean
    "never replace" — and a stale executor after a wire-protocol change
    degrades runs silently (2026-07: an old executor got an EMPTY MCP set).

    Deliberately conservative: a live run of the same user may not be using
    the executor at all (not yet at step 3, or a direct-trigger run that
    never does). Deferring costs one more turn on old code and self-corrects
    at the next ensure; replacing under a live run kills it (rule #14).
    """
    live = await live_run_elsewhere(
        user_id,
        exclude_run_id=active_run_id,
        caller="stale-replace",
        consequence="stale executor images will NOT roll",
    )
    return live is None


# The reaper's own binding of the shared liveness call. A partial rather
# than defaults on live_run_elsewhere: see that function on why the log
# subject cannot have a default.
_REAPER_LIVENESS = functools.partial(
    live_run_elsewhere,
    caller="reaper",
    consequence="executor idle-culling is OFF",
)


class _CullVeto:
    """The reaper's ``is_busy`` veto, with per-RUN audit de-duplication.

    A vetoed user KEEPS its idle stamp (that is the point — see
    ``claim_idle_users``), so it is re-offered and re-vetoed every pass for
    as long as its run lives. Auditing each of those would make the row count
    a function of RUN DURATION: one legitimate 10-hour agent (rule #14 says
    that is normal) would write ~300 rows and read as hundreds of near-misses.
    The metric counts runs saved, so each (user, run) is recorded once.

    Bounded by a hard cap rather than by expiry: entries are dropped when the
    user comes back clean, and a user that goes active in THIS process stops
    being a candidate without ever doing so. Evicting the oldest at the cap
    costs a duplicate audit row in a case that needs thousands of
    simultaneously-blocked users to reach.
    """

    _MAX_TRACKED = 4096

    def __init__(self, check=_REAPER_LIVENESS) -> None:
        self._check = check
        self._blocked_by: dict[str, str] = {}   # user_id -> blocking run id
        # Per-pass tallies. The veto is the only thing that sees the
        # candidates — claim_idle_users returns just the survivors, so
        # "nobody was due" and "everybody was vetoed" are the same empty list
        # from the reaper's side. Counted here, drained by take_pass_stats.
        self._judged = 0
        self._vetoed = 0
        self._blind = 0     # vetoes that were "could not tell", not "busy"

    def take_pass_stats(self) -> dict:
        stats = {
            "judged": self._judged, "vetoed": self._vetoed, "blind": self._blind,
        }
        self._judged = self._vetoed = self._blind = 0
        return stats

    async def __call__(self, user_id: str) -> bool:
        try:
            run_id = await asyncio.wait_for(
                self._check(user_id), _PER_CANDIDATE_S
            )
        except TimeoutError:
            # The lookup cannot time itself out — it would still be waiting on
            # the connection pool. Bounding it HERE keeps the tally below on
            # the fast path of a slow DB; bounding it only at the admission
            # layer cancels this coroutine mid-await, so nothing is counted
            # and the pass reports the same zeros as a healthy empty one.
            # asyncio.CancelledError is a BaseException and still propagates.
            logger.warning(
                f"[reaper] liveness lookup for user={user_id} exceeded "
                f"{_PER_CANDIDATE_S}s — treating as busy"
            )
            run_id = UNKNOWN_RUN
        self._judged += 1
        if run_id is None:
            self._blocked_by.pop(user_id, None)
            return False
        self._vetoed += 1
        if run_id == UNKNOWN_RUN:
            self._blind += 1
        if self._blocked_by.get(user_id) != run_id:
            if len(self._blocked_by) >= self._MAX_TRACKED:
                self._blocked_by.pop(next(iter(self._blocked_by)))
            logger.info(
                f"[reaper] skipping user={user_id}: run {run_id} is live in "
                f"another process (idle here, busy elsewhere)"
            )
            if run_id == UNKNOWN_RUN:
                # Nothing to audit ("could not tell" is not a run saved), so
                # the memo lands unconditionally — otherwise an unreadable
                # user would reprint the line above every single pass.
                self._blocked_by[user_id] = run_id
            elif await _audit(
                EVENT_CULL_SKIPPED_BUSY, user_id=user_id, run_id=run_id,
            ):
                # Memo only on a row that actually landed. Recorded before the
                # write, a pool that stalls past the write budget would lose
                # that row FOREVER: the next pass sees the same (user, run),
                # takes the `!=` branch as false and never tries again — and
                # each row is one run this guard saved, the whole point of the
                # metric. Failure leaves the memo unset, so the next pass
                # retries; success returns to one row per (user, run).
                self._blocked_by[user_id] = run_id
        return True


async def _audit(
    event_type: str,
    *,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> bool:
    """Best-effort audit row; True when it landed. Never raises — the
    observer must not break the pass it is observing.

    The bool is what lets callers retry: these rows are this guard's only
    durable evidence, and a caller that noted "already audited" before the
    write would drop a row permanently every time the pool stalled.

    Bounded, because this is a DB WRITE on the cull path and the recheck
    branch reaches it through the veto: unbounded, a wedged pool parks the
    whole pass, and a pass that never finishes never reports, so the reaper
    reads as "never ran" while it is in fact stuck. The budget lives in here
    rather than around the call sites — wrapping those would put a second
    timeout on values that already have one.
    """
    try:
        from narranexus.platform.repository.executor_audit_repository import (
            ExecutorAuditRepository,
        )
        from narranexus.platform.utils.db.db_factory import get_db_client

        async def _write() -> None:
            await ExecutorAuditRepository(await get_db_client()).record(
                event_type=event_type, user_id=user_id, run_id=run_id,
                detail=detail,
            )

        await asyncio.wait_for(_write(), _AUDIT_WRITE_S)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[reaper] audit {event_type} failed user={user_id}: {e}")
        return False


class ExecutorReaper:
    """Periodically stops executors whose user has been idle past the TTL."""

    def __init__(
        self,
        controller: AgentAdmissionController,
        stop_fn: StopFn,
        *,
        is_busy: Optional[BusyCheck],
        ttl_seconds: float = DEFAULT_IDLE_TTL_SEC,
        interval_seconds: float = DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._controller = controller
        self._stop_fn = stop_fn
        self.ttl_seconds = ttl_seconds
        self.interval_seconds = interval_seconds
        # Required, not defaulted: passing None means "trust this process's
        # local view alone", which is precisely the assumption behind the
        # 2026-07-31 incident. Only a caller that KNOWS it is the sole process
        # running agents (tests, single-process deployments) may pass None,
        # and making it say so out loud is the point.
        self._is_busy = is_busy
        # Consecutive passes in which every judged candidate came back
        # "could not tell". Drives the repeat warning and the L2 field.
        self._blind_passes = 0
        # Blind passes since a cull_disabled row last LANDED. Counted
        # separately from _blind_passes so the rate limit tracks successful
        # writes: keyed off the pass number alone, a first row lost to a
        # stalled pool would leave the whole first hour of an outage with no
        # trace at all — during the outage that trace is the thing being
        # reported on. Retries only while writes fail; one success returns to
        # the _BLIND_WARN_EVERY tick, so rows never become a function of
        # outage duration.
        self._passes_since_audit = 0

    async def _report_pass(
        self, claim: dict, recheck: dict, reaped: int, *, refused: int = 0
    ) -> None:
        """Publish one pass's outcome to the L2 surfaces. Never raises.

        ``claim`` is the veto's tally from the claim phase — one question per
        candidate — and ``recheck`` the second round of questions the
        survivors get before their stop. They are reported separately because
        a merged count answers no question anyone asks: a healthy pass that
        culls 5 idle users asks 10 times, and a reader seeing ``judged: 10,
        reaped: 5`` goes looking for 5 users that do not exist.
        """
        global _LAST_PASS, _LAST_PASS_AT
        judged, blind = claim.get("judged", 0), claim.get("blind", 0)
        # Claim-phase numbers only. A recheck that goes blind while the claim
        # phase read the DB fine is a hiccup, not a blind pass, and folding it
        # in here would raise the alarm on it.
        wholly_blind = judged > 0 and blind == judged
        if judged == 0:
            # No candidates ⇒ no information either way. Resetting here would
            # let an idle minute in an otherwise blind hour punch the counter
            # back to zero and defeat any threshold alert built on it.
            pass
        elif wholly_blind:
            self._blind_passes += 1
        else:
            self._blind_passes = 0
            self._passes_since_audit = 0
        _LAST_PASS = {
            "reaped": reaped,
            # Stops the broker refused because the container reported work.
            # Distinct from `vetoed` (this side's own DB verdict): non-zero
            # here means the container saw a holder the events table cannot.
            "refused_busy": refused,
            # Carried, not kept in a module global: staleness is judged
            # against it, and one fewer piece of resettable module state is
            # one fewer way for a test to leak into the next one.
            "interval_seconds": self.interval_seconds,
            # Reported unconditionally, including the None case: a reaper
            # culling on this process's local view alone is the 2026-07-31
            # configuration, and it must not read as "not running".
            "veto_installed": self._is_busy is not None,
            "blind_passes": self._blind_passes,
            "judged": judged,
            "vetoed": claim.get("vetoed", 0),
            "blind": blind,
            "recheck_judged": recheck.get("judged", 0),
            "recheck_vetoed": recheck.get("vetoed", 0),
        }
        _LAST_PASS_AT = monotonic()
        if not wholly_blind:
            return
        # Every candidate unreadable ⇒ nothing will be culled while this
        # lasts. Rate-limited so the state outlives a log rotation without
        # becoming noise, and the audit row rides the SAME tick — one row per
        # pass would make the row count a function of outage duration, the
        # shape this file already avoids for run duration.
        due = self._passes_since_audit == 0 or (
            self._passes_since_audit >= _BLIND_WARN_EVERY
        )
        if not due:
            self._passes_since_audit += 1
        else:
            logger.warning(
                f"[reaper] liveness unreadable for all {judged} judged "
                f"candidate(s) — "
                f"NO executor has been culled for {self._blind_passes} pass(es). "
                f"Check {RECORDING_DISABLED_ENV} and DB reachability; idle "
                f"executors accumulate until the broker refuses new ones."
            )
            # The cause travels in the row: a failed events read lands here
            # just fine (only a DB that cannot be reached AT ALL cannot, since
            # that write needs the client that just failed), so the reader
            # must not have to guess which of the two it was.
            from narranexus.platform.agent_runtime.run_recorder import (
                recording_enabled,
            )

            # Landed → back to the slow tick. Failed → the counter stays put,
            # so the next pass is due again and the row is retried.
            if await _audit(EVENT_CULL_DISABLED, detail={
                "judged": judged,
                "blind_passes": self._blind_passes,
                "recording_disabled": not recording_enabled(),
            }):
                self._passes_since_audit = 1

    async def reap_once(self) -> list[str]:
        """One cull pass. Returns the users whose executors were stopped.

        A stop failure for one user is logged and skipped; it never aborts the
        pass. Every path that claims a user without stopping it hands the idle
        stamp back — claiming is destructive, and this reaper is the only
        thing culling idle executors (the broker's label-based reaper collects
        ORPHANS, and a known user's container is not one), so a dropped stamp
        is a container that is never reclaimed.
        """
        users = await self._controller.claim_idle_users(
            self.ttl_seconds, is_busy=self._is_busy,
            # The worst case of ONE call, not just its lookup: __call__ can
            # follow the lookup with an audit write, and reserving only the
            # lookup leaves a window where the batch cancels the call on that
            # write instead — the tally survives (it is incremented before)
            # but the row is lost.
            per_check_budget=_PER_CANDIDATE_S + _AUDIT_WRITE_S,
        )
        # Drained HERE, before the rechecks below add a second question per
        # survivor — that split is what keeps "judged" equal to the candidate
        # count (see _report_pass).
        veto = self._is_busy if isinstance(self._is_busy, _CullVeto) else None
        claim_stats = veto.take_pass_stats() if veto else {}
        reaped: list[str] = []
        # Users the broker refused to stop because the CONTAINER reported work.
        refused = 0
        for user_id in users:
            try:
                # Re-checked per user, not once for the batch. claim_idle_users
                # vetoed everyone up front, but the stops run sequentially and
                # each `docker stop` waits out a SIGTERM grace — so by the time
                # user N is stopped its verdict can be minutes old, ample for a
                # bus-triggered run to have started and reached step 3 on that
                # very container. One indexed read against the whole bug
                # recurring at a lower rate.
                # Bounded like the claim phase, and by the same budget: both
                # go through _CullVeto.__call__, which wraps the lookup. Not
                # wrapped a second time here — two timeouts on the same value
                # race, and the loser's TimeoutError would land in the stop
                # handler below and read as "the broker failed".
                if self._is_busy is not None and await self._is_busy(user_id):
                    logger.info(
                        f"[reaper] user={user_id} became busy between the claim "
                        f"and the stop; leaving its executor alone"
                    )
                    # The claim already took its idle stamp, and just skipping
                    # would be the "claimed-then-skipped" leak claim_idle_users
                    # warns about — a user driven mostly from another process
                    # never gets a new stamp in THIS one.
                    await self._controller.restamp_idle(user_id)
                    continue
                if not await self._stop_fn(user_id):
                    # The broker refused: the CONTAINER reports work this side
                    # cannot see (an office-watch session leaves no run row).
                    # Same handling as the busy branch above — hand the stamp
                    # back, or a container we never stopped is also a container
                    # we never reconsider. Counted, so the admin surface does
                    # not report a cull that did not happen.
                    logger.info(
                        f"[reaper] broker refused to stop user={user_id}: the "
                        f"container is busy; leaving its executor alone"
                    )
                    refused += 1
                    await self._controller.restamp_idle(user_id)
                    # Deliberately NOT a cull_skipped_busy row: that metric's
                    # contract is "one row = one run this guard saved, named by
                    # its real run id", and this refusal has no run id to name
                    # (the holder is not a recorded run). Reported as a count
                    # instead, via reaper_status.
                    continue
                reaped.append(user_id)
            except Exception as e:  # noqa: BLE001 — best-effort, must not abort
                logger.warning(f"[reaper] failed to stop executor user={user_id}: {e}")
                # Same reasoning as the busy branch above, and the likelier of
                # the two: stop_fn is an HTTP call to the broker, so a deploy
                # restart or a 5xx lands here. Without the restamp the user's
                # stamp is gone for good and — for a user driven from another
                # process — its container is never reclaimed.
                #
                # Inside its own guard: restamp_idle only touches memory today,
                # but this handler's contract is that ONE user's failure never
                # aborts the pass, and that must not depend on what a
                # collaborator does later.
                try:
                    await self._controller.restamp_idle(user_id)
                except Exception as restamp_err:  # noqa: BLE001
                    logger.warning(
                        f"[reaper] restamp failed user={user_id}: {restamp_err}"
                    )
        if reaped:
            logger.info(f"[reaper] reaped {len(reaped)} idle executor(s): {reaped}")
        # Unconditional: a reaper running WITHOUT the veto is the one
        # configuration most worth seeing on the admin surface, and skipping
        # the report there would show it as "not running".
        await self._report_pass(
            claim_stats, veto.take_pass_stats() if veto else {}, len(reaped),
            refused=refused,
        )
        return reaped

    async def run_forever(self) -> None:
        logger.info(
            f"[reaper] started (ttl={self.ttl_seconds}s interval={self.interval_seconds}s)"
        )
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.reap_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[reaper] reap pass error: {e}")


def _on_reaper_done(task: "asyncio.Task") -> None:
    # Incident lesson #2: a fire-and-forget task must surface its death.
    global _TASK_ERROR
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Also parked for reaper_status: a single log line at death is L1 and
        # gets eaten by the next rotation, while the consequence (nothing
        # reclaims executors any more) lasts until someone restarts backend.
        _TASK_ERROR = repr(exc)
        logger.error(f"[reaper] background task died: {exc!r}")


def maybe_start_executor_reaper() -> Optional["asyncio.Task"]:
    """Start the reaper as a background task — cloud + broker only.

    No-op (returns None) on local/desktop, or whenever no broker is
    configured: there are no per-user executors to cull there.
    """
    from narranexus.platform.agent_framework.loop.broker_client import broker_url, stop_executor

    if not broker_url():
        return None
    ttl = int(os.getenv("EXECUTOR_IDLE_TTL_SEC", "") or DEFAULT_IDLE_TTL_SEC)
    interval = int(os.getenv("EXECUTOR_REAP_INTERVAL_SEC", "") or DEFAULT_INTERVAL_SEC)

    reaper = ExecutorReaper(
        get_admission_controller(), stop_executor,
        # This process is not the only one running agents.
        is_busy=_CullVeto(),
        ttl_seconds=ttl, interval_seconds=interval,
    )
    task = asyncio.create_task(reaper.run_forever())
    task.add_done_callback(_on_reaper_done)
    return task
