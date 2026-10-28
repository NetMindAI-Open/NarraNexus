"""
@file_name: patrol.py
@author:
@date: 2026-08-10
@description: Leader patrol — the platform half of "somebody owns this flow".

The team room is a purely @-driven model: every member, the lead included,
only wakes when addressed. So a flow advances only while each hop remembers to
@ the next one, and any hop that forgets, goes quiet, or has its @ stripped by
the cascade cap kills the chain — with, structurally, nobody to notice. This
module is the periodic activation that gives the flow an owner.

What lives here is the part that must NOT be a model's opinion: deciding an
item is stalled, and deciding a team is due for a patrol. The lead's judgement
begins after that, and (owner decision 2026-08-07) is limited to chasing —
never re-assigning, because "idle with unfinished work" is not the same as
"this one is never coming back", and a wrong re-assignment means two agents
working the same deliverable.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, List, Optional, Tuple

from loguru import logger

from narranexus.platform.message_bus._bus_activity import is_live
from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
from narranexus.platform.schema.team_schema import patrol_is_on
from narranexus.platform.schema.team_work_schema import (
    WorkItem,
    WorkItemStatus,
)
from narranexus.platform.utils.timezone import utc_now

#: Marks a patrol line in the transcript. The frontend renders it as the room
#: speaking rather than as the lead, and ``_team_cascade_depth`` skips it — a
#: patrol line is the platform taking stock, not an agent taking a turn.
PATROL_MSG_TYPE = "patrol"

#: How often a healthy board is looked at. Long on purpose — every patrol is a
#: real LLM turn, so the interval IS the cost.
PATROL_INTERVAL_S = 600

#: The pace once something is actually stuck. Shorter because this is the
#: window in which the flow is dead and nobody knows.
PATROL_STALLED_INTERVAL_S = 180


async def _clear_stale_stall(repo: TeamWorkItemRepository, item: WorkItem) -> None:
    """Drop a ``stalled`` verdict that no longer has evidence behind it.

    Two callers reach this from opposite directions — "the assignee came back"
    and "this row says nothing about the sweeper" — but the write is the same
    and so is the principle: a stall is a derived fact, so it lasts exactly as
    long as the evidence for it.

    Always back to ``in_progress``. The only item that lands somewhere it did
    not start is one a model pushed to ``open`` via ``team_work_update_status``
    while keeping its assignee; ``create_item`` already opens an assigned item
    as ``in_progress``.
    """
    if item.status == WorkItemStatus.STALLED:
        await repo.set_status(item.item_id, WorkItemStatus.IN_PROGRESS)


async def detect_stalled_items(
    db: Any, team_id: str, *, executor_agent_id: str
) -> List[WorkItem]:
    """Items whose assignee has gone quiet. Writes ``stalled`` through.

    The evidence is ``bus_agent_activity``, never a model's read of the room
    (iron rule #15). Three shapes count as stalled, and one very deliberately
    does not:

    * assignee is ``idle`` while the item is unfinished — the Dunhuang shape:
      acknowledged, then silence;
    * assignee claims ``running`` but its heartbeat died — a wedged worker;
    * assignee has no activity row at all — never started;
    * **a fresh ``running`` heartbeat is NOT stalled, however long it has been
      going.** A 25-minute turn is work. Chasing it would make the platform
      the interruption source for exactly the long-running run iron rule #14
      protects.

    Unclaimed items are excluded: nobody is late on a task nobody took. That
    needs handing out, which is a different prompt.

    ``executor_agent_id`` — the agent running this sweep — is excluded for a
    different reason: **its activity row is not evidence about its items.**
    That row describes the current turn, and the current turn is the sweep.
    Read before the sweep opens its row it says idle, so the sweeper would
    stall its own items on every single cycle, permanently, and the prompt
    would then instruct the lead to chase itself. Read after, it says running,
    so the sweeper's items could never stall. Neither reading carries any
    information about whether the item is progressing, and a verdict from
    absent evidence is worse than no verdict.

    The cost is real, accepted, and larger than it first looks: a lead stuck on
    its own item is caught by NOBODY. Patrol is one sweep per team run by that
    team's lead (``teams_due_for_patrol`` yields a single
    ``(team_id, lead, channel)`` per team), so there is no other sweep to fall
    back on. This is a known gap, not a covered case — closing it needs a
    second scanner, either another member or a platform-side pass, and this
    change does not add one.

    It is still the better of the two options on offer: the alternative was a
    permanent self-nag with no recovery path at all. And a stale ``stalled``
    does get cleared (below), so the gap is "nobody notices new trouble", not
    "an old verdict hangs forever".

    Recovery is written through too — an assignee that comes back leaves the
    stalled set, so patrol stops chasing someone already working again.
    """
    repo = TeamWorkItemRepository(db)
    # The recycle that used to happen here now runs in `teams_due_for_patrol`,
    # ahead of the gates that would skip a lead-less team entirely — see the
    # comment there. By the time this function is reached, expired rows are
    # already out of `ACTIVE`.
    items = await repo.list_active(team_id)
    stalled: List[WorkItem] = []
    for item in items:
        if not item.assignee_id:
            continue  # unclaimed: needs assigning, not chasing
        if executor_agent_id and item.assignee_id == executor_agent_id:
            # Pass no verdict on the sweeper — but do not leave an old one
            # standing either. A `stalled` recorded before this agent became
            # the sweeper would otherwise be permanent: skipping the item means
            # nothing can ever clear it, `ACTIVE` includes `STALLED` so the
            # team stays pinned at the fast pace forever, the user's panel
            # shows a stall that is not happening, and the item is excluded
            # from the returned list so the lead is never told to fix it by
            # hand. Reachable without contrivance: assign to B, B goes dark and
            # gets marked, then the owner makes B the lead.
            await _clear_stale_stall(repo, item)
            continue
        try:
            # Keyed by BOTH columns, because the table is: one row per
            # (agent_id, channel_id). Belonging to several teams is a supported
            # shape, so an agent has several rows, and `get_one` is LIMIT 1
            # with no ORDER BY — on MySQL the clustered PK makes that
            # "whichever channel_id sorts first", which has nothing to do with
            # this item. The effect was the feature's own founding case
            # inverted: a member who had abandoned this room while running a
            # turn in another read as live, so the item was never chased.
            row = await db.get_one(
                "bus_agent_activity",
                {"agent_id": item.assignee_id, "channel_id": item.channel_id},
            )
        except Exception as e:  # noqa: BLE001 — unreadable activity = unknown
            logger.debug(f"[patrol] activity lookup failed for {item.assignee_id}: {e}")
            continue
        if is_live(row):
            # Came back — clear a stall we may have recorded earlier.
            await _clear_stale_stall(repo, item)
            continue
        if item.status != WorkItemStatus.STALLED:
            await repo.set_status(item.item_id, WorkItemStatus.STALLED)
            # Logged on the TRANSITION only, so the closure report counts
            # stalls rather than sweeps — a stalled item is re-derived on
            # every cycle and a line per cycle would make one dead hand-off
            # look like hundreds. Read by scripts/diag_collector.
            logger.info(
                f"[work-item] action=stall item={item.item_id} "
                f"team={team_id} channel={item.channel_id} "
                f"assignee={item.assignee_id} origin={item.origin}"
            )
        item.status = WorkItemStatus.STALLED
        stalled.append(item)
    return stalled


def patrol_due_at(last_patrol_at: Optional[Any], has_stalled: bool) -> bool:
    """Whether a team's next patrol is due.

    Adaptive by design: a board where everything is running gets looked at
    rarely (minimum interference, minimum token burn), a board with something
    stuck gets looked at often (that is the window where the flow is dead and
    nobody knows).
    """
    if last_patrol_at is None:
        return True
    from narranexus.platform.utils.run_liveness import parse_db_utc

    last = parse_db_utc(last_patrol_at)
    if last is None:
        return True
    interval = PATROL_STALLED_INTERVAL_S if has_stalled else PATROL_INTERVAL_S
    return (utc_now() - last) >= timedelta(seconds=interval)


async def teams_due_for_patrol(db: Any) -> List[Tuple[str, str, str]]:
    """``(team_id, lead_agent_id, channel_id)`` for every team to patrol now.

    The candidate query of the patrol lane, shaped like
    ``_agents_with_pending``: one pass for the whole fleet rather than asking
    each team in turn whether it has anything to do.

    **Not a pure query.** It also recycles each candidate team's expired AUTO
    errands, and does so BEFORE the gates below — so the write happens for
    teams that never appear in the return value (a team with no lead is
    recycled and not patrolled). That placement is the point, not an accident:
    see the comment at the call site for why the recycle must sit upstream of
    ``patrol_is_on``.

    Three gates, in cost order:

    1. the team has unfinished work — an empty board produces NO patrol runs at
       all, which is the feature's entire cost guarantee;
    2. the team has a lead and has not switched patrol off — the platform does
       not appoint someone in charge on the user's behalf;
    3. the interval has elapsed (adaptive, see ``patrol_due_at``).
    """
    repo = TeamWorkItemRepository(db)
    team_ids = await repo.teams_with_active_work()
    if not team_ids:
        return []

    due: List[Tuple[str, str, str]] = []
    for team_id in team_ids:
        try:
            # Recycle expired errands BEFORE any of the patrol gates.
            #
            # This used to live inside `detect_stalled_items`, i.e. downstream
            # of every gate that decides whether this team is worth an LLM turn
            # — and two of those gates are permanent, not throttles:
            # `patrol_is_on` requires a lead, and `lead_agent_id` defaults to
            # None. So a team nobody has named a lead for (the state every team
            # is created in) opened errands from every @mention and never
            # recycled one.
            #
            # The opening side has no gate at all, so the two must not be
            # asymmetric. `teams_with_active_work()` above is the right scope:
            # it looks at neither the lead nor `patrol_enabled`, so one call
            # here covers patrolled and unpatrolled teams alike — without
            # adding a second scheduler for one sweep.
            #
            # `expire_stale_errands` swallows its own failures, which matters
            # more here than it did before: the per-team `except` below would
            # otherwise let one failed recycle skip this team's whole patrol,
            # trading a stall detection for a bookkeeping error.
            from narranexus.platform.message_bus.errand import expire_stale_errands

            # The board is read ONCE and handed to the recycle, rather than
            # read inside it and again here. This loop runs every poll cycle
            # (3-12s), not once per patrol (180-600s), so a duplicated read is
            # per-team standing overhead on the serial prefix of every message
            # dispatch.
            board = await repo.list_active(team_id)
            expired = await expire_stale_errands(db, team_id, candidates=board)

            team = await db.get_one("teams", {"team_id": team_id})
            if not team:
                continue
            lead = str(team.get("lead_agent_id") or "")
            # One rule, one implementation — it also covers "no lead means no
            # patrol", so this single call replaces both checks.
            if not patrol_is_on(team):
                continue
            # What the recycle left, never what preceded it. `has_stalled`
            # picks the 600s or 180s cadence and `items[0].channel_id` aims the
            # patrol at a room, so a row cancelled a moment ago must drive
            # neither. Filtered in memory rather than re-read: `expire_stale_
            # errands` already told us exactly which ids it retired.
            items = [i for i in board if i.item_id not in set(expired)]
            if not items:
                continue
            has_stalled = any(i.status == WorkItemStatus.STALLED for i in items)
            if not patrol_due_at(team.get("last_patrol_at"), has_stalled):
                continue
            # Every active item of a team carries the same room.
            due.append((team_id, lead, items[0].channel_id))
        except Exception as e:  # noqa: BLE001 — one bad team never stops the sweep
            logger.warning(f"[patrol] candidate check failed for {team_id}: {e}")
    return due


async def mark_patrolled(db: Any, team_id: str) -> None:
    """Stamp the patrol cursor. Called however the patrol turn ended.

    Stamped on EXIT rather than on success: a patrol that crashed still
    consumed its slot, and re-running it immediately would turn one broken
    team into a hot loop.
    """
    try:
        await db.update("teams", {"team_id": team_id}, {"last_patrol_at": utc_now()})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] could not stamp cursor for {team_id}: {e}")


# --- Patrol's own speech limit ---------------------------------------------
#
# Patrol messages are exempt from the cascade-depth cap (owner decision
# 2026-08-07, option a), which is what makes a chase @ actually reach its
# target in the one situation that matters: a dead flow IS a long unbroken run
# of agent messages, so depth is already at the cap and the @ would otherwise
# be stripped without a trace.
#
# The exemption removes patrol from the runaway-@ protection, so this counter
# is the only backstop left — and it lives on DISK. The bus's existing limiter
# (`MessageBusTrigger._rate_counters`) is an in-memory dict keyed on
# `time.monotonic()`; it is fine for damping a chatty agent and useless here,
# because a workers restart would hand a confused model a fresh budget.

#: Patrol messages allowed per team per window.
PATROL_SPEECH_MAX = 6

#: The window, in seconds.
PATROL_SPEECH_WINDOW_S = 1800


async def may_patrol_speak(db: Any, team_id: str) -> bool:
    """Whether patrol may post into this team's room right now.

    Fails OPEN. Patrol's job is telling the owner something is wrong, so
    silencing it because its own bookkeeping row could not be read would drop
    the message in the very case it matters most. A wrong "allow" costs one
    extra line in a room; a wrong "deny" costs a flow that dies unannounced.
    """
    try:
        row = await db.get_one("teams", {"team_id": team_id})
        if not row:
            return True
        if not _within_speech_window(row.get("patrol_spoke_at")):
            return True  # window rolled over — the count is stale
        return int(row.get("patrol_spoke_count") or 0) < PATROL_SPEECH_MAX
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] speech-limit check failed for {team_id}: {e}")
        return True


async def note_patrol_spoke(db: Any, team_id: str) -> None:
    """Record one patrol message. Starts a new window when the old one lapsed."""
    try:
        row = await db.get_one("teams", {"team_id": team_id})
        if not row:
            return
        count = (
            int(row.get("patrol_spoke_count") or 0) + 1
            if _within_speech_window(row.get("patrol_spoke_at"))
            else 1
        )
        await db.update(
            "teams",
            {"team_id": team_id},
            {"patrol_spoke_at": utc_now(), "patrol_spoke_count": count},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[patrol] could not record speech for {team_id}: {e}")


def _within_speech_window(spoke_at: Optional[Any]) -> bool:
    if spoke_at is None:
        return False
    from narranexus.platform.utils.run_liveness import parse_db_utc

    last = parse_db_utc(spoke_at)
    if last is None:
        return False
    return (utc_now() - last) < timedelta(seconds=PATROL_SPEECH_WINDOW_S)
