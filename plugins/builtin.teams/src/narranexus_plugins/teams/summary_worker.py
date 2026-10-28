"""
@file_name: summary_worker.py
@author: NarraNexus
@date: 2026-08-10
@description: Background worker that keeps each team's progress summary fresh.

The bulletin's third source, after the user and the agents: "what is the team
actually up to", so a member joining a long task does not have to reconstruct
it from twenty messages of scrollback.

Shaped after `MemoryConsolidationWorker`, which is the codebase's one proven
pattern for background LLM work: poll loop, per-item isolation, and a failure
that never propagates. Iron rule #14 applies directly — this is opportunistic
work that must never force-stop or delay a turn.

**Why not have an agent do it.** Asking the lead agent to summarise would spend
a user-configured agent slot, produce a turn the user did not ask for, and put
the summary in that agent's history but nobody else's — which is precisely the
asymmetry the bulletin exists to remove. The summary belongs to the team, so
the platform writes it.

**Why no counter column.** The trigger is "how many messages since the last
summary", computed live against `idx_bus_msg_channel_time`. A counter is a
second source of truth that drifts the first time a message is deleted or a
wipe runs; the live count has nothing to keep in sync and nothing to repair.

The high-water mark is a TIMESTAMP because `bus_messages` has no monotonic id —
`message_id` is a random string and the table's only ordering is `created_at`,
which is also what its index covers. Messages sharing a timestamp can therefore
be counted one out; that is acceptable for a "have 15 things happened" trigger
and would not be for anything that must not skip a row.

**Why failure keeps the old summary.** Blanking on failure would convert one
provider hiccup into the loss of the team's only shared progress view, and an
empty summary does not read as "unknown" to the next reader — it reads as "this
team has made no progress".

**Why the cap truncates instead of refusing.** The opposite of the policy for
user entries, deliberately. A user's rule is never silently shortened, because
they would go on believing the whole rule is in force. Nobody depends on the
exact wording of a generated paragraph, and refusing an over-long one outright
would leave the team with no progress view at all.

**Why a failed team backs off.** The poll is 60 s and "keep the previous
summary" says nothing about how soon to try again. Retrying at full poll rate
turned one owner's empty balance into ~1,000 identical error lines an hour for
three days (2026-09-07 prod): the call cannot succeed until something outside
this process changes, and asking every minute only buries the log. A team that
failed waits an exponentially growing, capped interval (5 min → 6 h, the
shared `utils/backoff.py` formula) before it is tried again. A model that was
called and answered with nothing waits too, but on CONTENT, not time: asked
again about the same transcript it would answer the same way, and each ask is
billed — yet the moment the room has moved another `MESSAGE_THRESHOLD` messages
past what the model was shown, the input has changed and it is asked at once.
A timed wait there would have let a room that chatted idly for a while climb
to the 6 h cap and then keep its bulletin stale through an afternoon of real
work. Only a written summary, or a pass that never reached the model, clears
the wait. The wait is kept in memory on purpose — a restart is a fine reason to
try once more — but every failure also goes through
`alert_background_llm_failure`, so it leaves a `service_audit` row and, for a
credential or out-of-credit error, a deduplicated owner inbox notice: quieting
the log must not leave an owner with an empty balance and no way to find out.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from narranexus.platform.repository import TeamMemberRepository
from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES as _SYSTEM_MSG_TYPES,
    placeholders as _placeholders,
)
from narranexus.platform.schema.team_schema import (
    BULLETIN_MAX_SUMMARY_CHARS,
    BULLETIN_SOURCE_SUMMARY,
    TEAM_ROOM_OWNER_PREFIX,
    resolve_default_responder,
)
from narranexus.platform.agent_framework.llm.helper_sdk import get_helper_sdk
from narranexus.platform.services.background_llm_alerts import (
    alert_background_llm_failure,
)
from narranexus.platform.utils.backoff import compute_cooldown_seconds
from narranexus.platform.utils.cost_tracker import clear_cost_context, set_cost_context


_INSTRUCTIONS = (
    "You are summarising a team's group chat so every teammate can see where "
    "the work stands at the start of their next turn.\n"
    "\n"
    "Write 3-6 short lines covering: what has been done, what is in progress "
    "and who is on it, and anything the team is blocked on or waiting for.\n"
    "\n"
    "Rules:\n"
    "- Report only what the transcript shows. Do not infer progress that is "
    "not stated, and do not fill gaps with plausible-sounding steps.\n"
    "- Write STATE, not instructions. This is shown next to the team's actual "
    "rules, and a sentence phrased as a command would be followed as one.\n"
    "- No greetings, no preamble, no meta-commentary about the summary.\n"
    "- If the transcript shows no substantive work, reply with nothing at all."
)


async def _inject_team_credentials(team_id: str, db) -> None:
    """Put the TEAM OWNER's effective LLM config onto this task's ContextVars.

    The background twin of what `auth_middleware` does per request, and the step
    `MemoryConsolidationWorker` learned to take the hard way: this worker runs in
    the backend lifespan, outside any HTTP request, so no ContextVar injection
    happens and the helper call falls through `_ConfigProxy` to the platform
    global. In cloud that is the 2026-07 incident — an expired platform key 401'd
    every background helper call for about two weeks while long-term memory
    silently degraded. Local desktop is unaffected (the resolver is a no-op
    there), which is exactly why it can pass every local test.

    Resolved by the team's OWNER rather than by some member agent. The summary is
    platform work for the team, not one agent's errand; picking a member would
    arbitrarily borrow that agent's model override for output nobody asked it
    for, and the owner is who pays either way.

    CLEAR FIRST. `run_once` walks every team in sequence in one task, so without
    a reset a team whose owner cannot be resolved would inherit the previous
    team's credentials — a cross-tenant leak, not merely a stale config.
    """
    from narranexus.platform.agent_framework.providers.resolver import (
        inject_user_helper_credentials,
    )

    team = await db.get_one("teams", {"team_id": team_id})
    owner = (team or {}).get("owner_user_id")
    if not owner:
        logger.warning(
            f"[team.summary] team {team_id} has no owner row — helper "
            f"credentials left cleared (global fallback)."
        )
    # Called even with an empty owner: the clear-first step is the whole point
    # of routing through the shared helper, and skipping it on the unresolvable
    # path is exactly how the previous tenant's credentials would survive.
    await inject_user_helper_credentials(owner or "", db)


class _Outcome(Enum):
    """What one team's attempt came to — three states, not a bool, because
    "the model was asked and said nothing" must back off like a failure while
    "the room was not due" must not."""

    WRITTEN = "written"
    NOT_DUE = "not_due"
    EMPTY_REPLY = "empty_reply"


@dataclass(frozen=True)
class _Attempt:
    """`_summarise_team`'s result. `newest` is the timestamp of the newest
    message the model was shown — set whenever the model was asked, because an
    EMPTY_REPLY waits for the room to move past exactly that point."""

    outcome: _Outcome
    newest: Optional[str] = None


@dataclass(frozen=True)
class _Wait:
    """Why a team is not tried this pass — one of two independent waits.

    A FAILURE wait (`shown_newest` None) is timed: `next_at` on the worker's
    clock, earned by `failures` consecutive failed attempts. An EMPTY-REPLY wait
    (`shown_newest` set) is content-gated: it ends when the room holds
    `MESSAGE_THRESHOLD` non-system messages newer than `shown_newest`. An empty
    reply means the call itself worked, so it replaces (and thereby resets) a
    failure streak; a failure after an empty reply starts its streak at 1.
    """

    next_at: float = 0.0
    failures: int = 0
    shown_newest: Optional[str] = None


class TeamSummaryWorker:
    """One worker per process; polls every team room on an interval."""

    POLL_INTERVAL = 60.0
    # New messages since the last summary before it is worth a call.
    MESSAGE_THRESHOLD = 15
    # How much scrollback the summariser is shown.
    TRANSCRIPT_LIMIT = 60
    # After a failed attempt, how long before the team is tried again: BASE
    # doubled per consecutive failure, never more than MAX. Ints on
    # purpose — `compute_cooldown_seconds` then stays in arbitrary-precision
    # integer arithmetic and a long streak cannot overflow.
    BACKOFF_BASE = 300
    BACKOFF_MAX = 6 * 3600

    def __init__(self, db_client: Any, *, poll_interval: float = POLL_INTERVAL):
        self._db = db_client
        self.poll_interval = poll_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        # Last pass's outcome, for the `pass:` log line and tests. `backoff` is
        # the number of teams skipped because they are waiting out a miss.
        self.last_pass: Dict[str, int] = {"rooms": 0, "summarised": 0, "failed": 0, "backoff": 0}
        # team_id → why it is waiting (`_Wait`). Monotonic seconds; tests swap
        # the clock.
        self._backoff: Dict[str, _Wait] = {}
        self._clock: Callable[[], float] = time.monotonic
        # team_id → cost bearer resolved by `_summarise_team` during the
        # current attempt, so `_report_failure` does not resolve it again.
        self._attempt_bearer: Dict[str, str] = {}

    # ── lifecycle (mirrors services/memory_consolidation_worker.py) ─────────

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[team.summary] worker started")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[team.summary] worker stopped")

    async def _run_loop(self) -> None:
        while self.running:
            try:
                await self.run_once()
            except Exception as e:  # noqa: BLE001 — a pass must never kill the loop
                logger.exception(f"[team.summary] pass failed: {e}")
            await asyncio.sleep(self.poll_interval)

    # ── core ────────────────────────────────────────────────────────────────

    async def run_once(self) -> int:
        """One pass over every team room. Returns how many teams were summarised.

        Per-team isolation, like the memory worker's per-scope isolation: one
        unsummarisable room must not stall every other room.
        """
        summarised = 0
        failed = 0
        backoff = 0
        rooms = await self._team_rooms()
        for room in rooms:
            team_id = room["team_id"]
            try:
                waiting = await self._in_backoff(team_id, room["channel_id"])
            except Exception as e:  # noqa: BLE001 — isolate the bad team
                # The content gate reads the DB; one unreadable room must not
                # stall the rest. Stays waiting — its entry is untouched.
                logger.warning(f"[team.summary] team {team_id}: wait check failed: {e}")
                waiting = True
            if waiting:
                backoff += 1
                continue
            self._attempt_bearer.pop(team_id, None)
            try:
                attempt = await self._summarise_team(team_id, room["channel_id"])
            except Exception as e:  # noqa: BLE001 — isolate the bad team
                failed += 1
                wait = self._note_failure(team_id)
                logger.warning(
                    f"[team.summary] team {team_id} failed, keeping its previous "
                    f"summary; next attempt in {wait}s: {e}"
                )
                # Only on a real attempt, never for a team skipped above —
                # otherwise a waiting team would alert on every poll.
                await self._report_failure(team_id, e)
                continue
            if attempt.outcome is _Outcome.EMPTY_REPLY:
                self._backoff[team_id] = _Wait(shown_newest=attempt.newest)
                logger.info(
                    f"[team.summary] team {team_id}: model had nothing to say; "
                    f"next attempt after {self.MESSAGE_THRESHOLD} more messages"
                )
                continue
            if attempt.outcome is _Outcome.WRITTEN:
                summarised += 1
            self._backoff.pop(team_id, None)
        self._forget_vanished_teams(rooms)
        self._attempt_bearer.clear()
        # An L2 heartbeat, not decoration. With only per-failure warnings, "every
        # room is quiet" and "every room is failing" are the same observation:
        # silence. This distinguishes them, and it is the signal that would have
        # exposed the two production-only faults review had to find by reading —
        # the worker returned 0 forever while looking perfectly healthy.
        self.last_pass = {
            "rooms": len(rooms), "summarised": summarised, "failed": failed, "backoff": backoff,
        }
        # `backoff` is in the gate: a team waiting out a failure is the steady
        # state this counter exists for, and it has failed=0 and summarised=0.
        # An empty deployment (all three zero) stays silent.
        if failed or summarised or backoff:
            logger.info(
                f"[team.summary] pass: rooms={len(rooms)} summarised={summarised} "
                f"failed={failed} backoff={backoff}"
            )
        return summarised

    # ── failure backoff ─────────────────────────────────────────────────────

    async def _in_backoff(self, team_id: str, channel_id: str) -> bool:
        """Is this team waiting? A failure waits on the clock; an empty reply
        waits until the room has moved `MESSAGE_THRESHOLD` messages past what
        the model was shown (one indexed COUNT per waiting team per pass)."""
        entry = self._backoff.get(team_id)
        if entry is None:
            return False
        if entry.shown_newest is not None:
            moved = await self._new_message_count(channel_id, entry.shown_newest)
            return moved < self.MESSAGE_THRESHOLD
        return self._clock() < entry.next_at

    def _note_failure(self, team_id: str) -> int:
        """Record one more consecutive failure; return the wait it earned.

        Only a failure streak counts — an empty-reply entry carries 0, so a
        failure right after one starts at BASE."""
        entry = self._backoff.get(team_id)
        failures = (entry.failures if entry is not None else 0) + 1
        wait = compute_cooldown_seconds(failures, base=self.BACKOFF_BASE, cap=self.BACKOFF_MAX)
        self._backoff[team_id] = _Wait(next_at=self._clock() + wait, failures=failures)
        return wait

    async def _report_failure(self, team_id: str, error: Exception) -> None:
        """Leave a durable trace of a failed attempt and, when the owner can fix
        it, tell them. Never raises: this runs in `run_once`'s except block, and
        an exception here would abort every later team in the pass.

        The cost bearer `_summarise_team` already resolved in this attempt is
        reused; it is looked up here only when the attempt failed before that
        point. The lookups are wrapped separately because the alerter only
        promises not to raise itself; a DB that is failing would make them
        raise before it is ever called. An empty agent id still leaves the
        audit row.
        """
        owner: Optional[str] = None
        bearer = self._attempt_bearer.pop(team_id, "")
        try:
            team = await self._db.get_one("teams", {"team_id": team_id})
            owner = (team or {}).get("owner_user_id") or None
            if not bearer:
                bearer = await self._cost_bearer(team_id)
        except Exception as lookup_error:  # noqa: BLE001 — observer never breaks observed
            logger.warning(f"[team.summary] owner lookup for {team_id} failed: {lookup_error}")
        try:
            await alert_background_llm_failure(
                agent_id=bearer,
                source="team_summary",
                error=error,
                owner_user_id=owner,
                source_id=team_id,
            )
        except Exception as alert_error:  # noqa: BLE001 — same reason
            logger.warning(f"[team.summary] failure alert for {team_id} failed: {alert_error}")

    def _forget_vanished_teams(self, rooms: List[Dict[str, str]]) -> None:
        """A deleted team's room is gone from `_team_rooms`, so nothing would
        ever clear its entry; drop entries for teams no longer listed."""
        live = {room["team_id"] for room in rooms}
        for team_id in [t for t in self._backoff if t not in live]:
            del self._backoff[team_id]

    async def _team_rooms(self) -> List[Dict[str, str]]:
        """Every team room channel, paired with the team it belongs to.

        The team is recovered from the channel's ``created_by`` marker
        (``team_<id>``), which is how the rest of the codebase identifies a team
        room — no extra column, and no join that could disagree with it.
        """
        rows = await self._db.execute(
            "SELECT channel_id, created_by FROM bus_channels WHERE channel_type = %s",
            ("group",),
            fetch=True,
        )
        out: List[Dict[str, str]] = []
        for r in rows or []:
            marker = r.get("created_by") or ""
            if marker.startswith(TEAM_ROOM_OWNER_PREFIX):
                out.append(
                    {
                        "team_id": marker[len(TEAM_ROOM_OWNER_PREFIX) :],
                        "channel_id": r["channel_id"],
                    }
                )
        return out

    async def _new_message_count(self, channel_id: str, since: Optional[str]) -> int:
        """Messages in this room newer than the last summarised one.

        System lines are excluded. They are not team activity, and one of them
        is the bulletin notice THIS FEATURE writes into the same room — counting
        it lets a quiet team be pushed over the threshold by the announcement of
        its own last summary, i.e. the platform triggering itself.
        """
        if since is None:
            rows = await self._db.execute(
                "SELECT COUNT(*) AS n FROM bus_messages "
                f"WHERE channel_id = %s AND msg_type NOT IN ({_placeholders()})",
                (channel_id, *_SYSTEM_MSG_TYPES),
                fetch=True,
            )
        else:
            rows = await self._db.execute(
                "SELECT COUNT(*) AS n FROM bus_messages "
                f"WHERE channel_id = %s AND created_at > %s AND msg_type NOT IN ({_placeholders()})",
                (channel_id, since, *_SYSTEM_MSG_TYPES),
                fetch=True,
            )
        return int((rows or [{"n": 0}])[0].get("n") or 0)

    async def _summarise_team(self, team_id: str, channel_id: str) -> _Attempt:
        """Summarise one team if it has moved enough."""
        repo = TeamBulletinRepository(self._db)
        existing = await repo.get_summary(team_id)
        watermark = await self._watermark(team_id)

        if await self._new_message_count(channel_id, watermark) < self.MESSAGE_THRESHOLD:
            # Nothing new worth a call. An unchanged room re-summarised on every
            # poll would bill the user to rewrite the same paragraph.
            return _Attempt(_Outcome.NOT_DUE)

        transcript, newest = await self._transcript(channel_id)
        if not transcript.strip():
            return _Attempt(_Outcome.NOT_DUE)

        # No member means no cost bearer, and every helper SDK discards a record
        # whose agent id is empty — so summarising here would burn the owner's
        # tokens with nothing written down anywhere. The docstring used to say
        # this case "has nothing to summarise either"; that was an assertion
        # about the world rather than something the code enforced. Now it is the
        # gate, and the empty-bearer path is unreachable instead of merely
        # unlikely.
        bearer = await self._cost_bearer(team_id)
        self._attempt_bearer[team_id] = bearer
        if not bearer:
            logger.debug(
                f"[team.summary] team {team_id} has no members — skipping "
                f"(no cost bearer, so the tokens would go unrecorded)"
            )
            return _Attempt(_Outcome.NOT_DUE)

        text = await self._summarise(team_id=team_id, transcript=transcript, bearer=bearer)
        # A model that returned whitespace has told us nothing. Writing it would
        # replace a real summary with a blank one, which reads as "no progress".
        # Nor can the watermark advance: a team never summarised has no row to
        # hold it. So the caller holds this team until the room moves past
        # `newest` — the same transcript would get the same non-answer, billed,
        # on every poll.
        if not (text or "").strip():
            return _Attempt(_Outcome.EMPTY_REPLY, newest)

        await repo.upsert_summary(team_id, (text or "").strip()[:BULLETIN_MAX_SUMMARY_CHARS])
        await self._set_watermark(team_id, newest)
        logger.info(f"[team.summary] team={team_id} summarised ({'refreshed' if existing else 'first'})")
        return _Attempt(_Outcome.WRITTEN, newest)

    async def _transcript(self, channel_id: str) -> tuple[str, Optional[str]]:
        """The room's recent messages as plain text, plus the newest timestamp.

        Returned together so the watermark advances to exactly what the model
        was shown — advancing past unseen messages would silently skip them.
        """
        # Same exclusion as the trigger: feeding "Team bulletin updated." into
        # the summariser invites it to report the platform's own bookkeeping as
        # team progress.
        rows = await self._db.execute(
            "SELECT created_at, from_agent, content FROM bus_messages "
            f"WHERE channel_id = %s AND msg_type NOT IN ({_placeholders()}) "
            "ORDER BY created_at DESC LIMIT %s",
            (channel_id, *_SYSTEM_MSG_TYPES, self.TRANSCRIPT_LIMIT),
            fetch=True,
        )
        rows = list(reversed(rows or []))
        if not rows:
            return "", None
        lines = [f"{r.get('from_agent')}: {r.get('content') or ''}" for r in rows]
        return "\n".join(lines), str(rows[-1]["created_at"])

    # ── the watermark ───────────────────────────────────────────────────────
    #
    # Lives in `team_bulletin_entries.watermark_at`, set only on the summary
    # row. A dedicated column rather than a reused one: `author_id` already
    # means "who wrote this", and giving it a second meaning that depends on
    # `source` is what makes a schema unreadable six months later.

    async def _watermark(self, team_id: str) -> Optional[str]:
        rows = await self._db.execute(
            "SELECT watermark_at FROM team_bulletin_entries WHERE team_id = %s AND source = %s",
            (team_id, BULLETIN_SOURCE_SUMMARY),
            fetch=True,
        )
        if not rows:
            return None
        return rows[0].get("watermark_at") or None

    async def _set_watermark(self, team_id: str, newest: Optional[str]) -> None:
        if not newest:
            return
        summary = await TeamBulletinRepository(self._db).get_summary(team_id)
        if summary is None:
            return
        await self._db.update(
            "team_bulletin_entries",
            {"entry_id": summary.entry_id},
            {"watermark_at": newest},
        )

    # ── the LLM call (test seam) ────────────────────────────────────────────

    async def _summarise(self, *, team_id: str, transcript: str, bearer: str) -> str:
        """Ask the helper LLM for the summary text.

        Goes through `get_helper_sdk()` so the platform is not bound to one
        provider or framework (iron rule #9), and through the cost context so
        the work is attributed rather than invisible.

        Most tests replace this method wholesale. That is convenient and it is
        also how two production-only faults survived a green suite: the cost
        context was called with keyword arguments the function does not accept,
        and the owner's credentials were never resolved. `tests/services/
        test_team_summary_worker.py` now exercises this body with only the SDK
        faked, which is the smallest seam that still runs the assembly below.
        """
        from narranexus.platform.agent_framework.providers.resolver import clear_user_config

        # Detached background task: no per-request ContextVars, so without this
        # every cloud call falls through to the platform key.
        await _inject_team_credentials(team_id, self._db)

        # A cost record is keyed on an AGENT, and every helper SDK drops the
        # record outright when the agent id is empty (`if not _agent_id or not
        # _db: return`) — before even the warn-on-missing-usage call, so an
        # empty id produces neither a row nor the L2 warning that exists to
        # catch exactly that. Writing `set_cost_context("")` would therefore
        # have been worse than writing nothing: the next reader sees a cost
        # context and assumes the tokens are accounted for.
        #
        # So the team's designated responder bears it. That is a mild
        # misattribution — the agent did not ask for this summary — and it is
        # the better of the two available errors, because the alternative is an
        # invisible hole in the owner's token spend — which this codebase
        # already calls its largest silent accounting gap.
        set_cost_context(bearer, self._db)
        try:
            sdk = get_helper_sdk()
            result = await sdk.llm_function(
                instructions=_INSTRUCTIONS,
                user_input=transcript,
                agent_id=bearer,
                db=self._db,
            )
            return getattr(result, "final_output", "") or ""
        finally:
            clear_cost_context()
            # Symmetric with the line above. Without it this task keeps the last
            # team's LLM config on its ContextVars until the next pass clears
            # it; harmless only because nothing else runs in this task, which is
            # a fact about today rather than a guarantee from the code.
            clear_user_config()

    async def _cost_bearer(self, team_id: str) -> str:
        """The agent a team summary's tokens are recorded against.

        Delegates to `resolve_default_responder` — the same rule the room uses
        for "who answers when nobody is @mentioned" — so the cost lands on the
        member the team already treats as its default. This was a second
        hand-written copy of that rule plus its own raw `team_members` query;
        one rule with two implementations is one that drifts.

        Empty when the team has no members, which `_summarise_team` treats as a
        reason not to summarise at all.
        """
        team = await self._db.get_one("teams", {"team_id": team_id})
        members = await TeamMemberRepository(self._db).list_members_by_team(team_id)
        return resolve_default_responder((team or {}).get("lead_agent_id"), members) or ""


# ---- plugin contribution (batch 3c.2): builtin.teams provides this worker through
# backend.workers with host="backend"; backend.main starts/stops it from the
# registry (no direct TeamSummaryWorker reference in the platform).
import asyncio as _asyncio  # noqa: E402

from narranexus.contracts.worker import WorkerSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402


class _BackendHandle:
    """Adapts the start()/stop() worker to the WorkerHandle shape (run awaits until stop())."""

    def __init__(self, worker: "TeamSummaryWorker") -> None:
        self._worker = worker
        self._done = _asyncio.Event()
        self.run = self._run()

    async def _run(self) -> None:
        await self._worker.start()
        await self._done.wait()
        await self._worker.stop()

    def stop(self) -> None:
        self._done.set()


async def _factory(ctx):
    return _BackendHandle(TeamSummaryWorker(ctx.db))


WORKERS = (Contribution("team_summary", lambda: WorkerSpec("team_summary", _factory, host="backend")),)
