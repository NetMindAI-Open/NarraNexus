"""
@file_name: team_rooms.py
@author:
@date: 2026-08-17
@description: One answer to "where is this team's room", and one way to open it.

A team room IS a bus group channel whose ``created_by`` is the synthetic
``team_<id>`` marker. That marker is load-bearing: it is a NON-AGENT value, so
``MessageBusTrigger._should_process_message``'s "the channel owner is always
activated" rule can never match a member, and delivery in a team room stays
purely @-mention driven.

The convention had grown four independent implementations — the work-board
tools, the bulletin notifier, the teams route, and (newest) the job origin
resolver. Four copies of a convention is three chances for them to disagree the
day it changes: give a team a second room, or add an ``is_primary`` flag, and
whoever misses a copy ships a feature that resolves to a different room than the
rest of the product. The newest copy had already drifted before it landed, which
is what prompted the consolidation.

Provisioning joined it here for a different reason: agents gained ``create_team``,
and a core-package MCP tool cannot import from ``backend`` without inverting the
layering. Keeping the lookup and the creation in one file also means the marker
is written in exactly one place and read in exactly one place.

## What is NOT folded in here

``plugins/builtin.teams/src/narranexus_plugins/teams/routes.py`` resolves rooms for MANY teams in ONE query
(``created_by IN (...)``). It looks like another copy and is not: it answers a
different question, and rewriting it in terms of ``primary_room_of`` would turn
one indexed query into N — the N+1 shape this repository's repository layer
exists to avoid. It shares the CONVENTION, via ``team_room_marker``; it does not
share the code.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from loguru import logger

from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX


def team_room_marker(team_id: str) -> str:
    """The synthetic ``created_by`` that identifies a team's room.

    The one place the marker string is composed. Batched callers that cannot
    use ``primary_room_of`` (see module docstring) still build their ``IN (...)``
    list from this, so the convention stays single-sourced even where the query
    cannot be.
    """
    return f"{TEAM_ROOM_OWNER_PREFIX}{team_id}"


async def primary_room_of(db: Any, team_id: str) -> Optional[str]:
    """The channel id of ``team_id``'s room, or None.

    The single-team form of the convention, and the ONLY read path — callers
    that need a falsy-string shape normalise at their own call site rather than
    getting a second function here, because two names for one lookup is how the
    four copies started.

    None means "no room", never an empty string: every caller uses the result
    as a write target, and half an answer would send a row to ``""``. A team
    whose room does not exist yet is a normal state, not an error — the room is
    created lazily by ``get_or_create_team_room``.

    Never raises: the callers all treat "no room" as a reason to decline quietly
    (no board, no bulletin notice, no job origin, no post), and none of them can
    do anything useful with an exception.
    """
    if not team_id:
        return None
    try:
        row = await db.get_one(
            "bus_channels",
            {"created_by": team_room_marker(team_id), "channel_type": "group"},
        )
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"[team-rooms] lookup failed for {team_id}: {e}")
        return None
    return (row or {}).get("channel_id") or None


async def get_or_create_team_room(
    db: Any,
    bus: Any,
    *,
    team_id: str,
    team_name: str,
    member_agent_ids: Sequence[str],
) -> str:
    """Find (or create) the team's room and sync its members. Returns channel_id.

    Membership is synced on every call because ``team_members`` is the source of
    truth and ``bus_channel_members`` is what delivery actually reads. The two
    drift the moment a member is added or removed, and this is where they are
    reconciled.
    """
    marker = team_room_marker(team_id)
    channel_id = await primary_room_of(db, team_id)
    if not channel_id:
        # `create_channel` sets created_by = members[0]; rewrite it immediately
        # to the non-agent marker so no member becomes the always-activated
        # channel owner.
        channel_id = await bus.create_channel(
            name=team_name or "Team",
            members=list(member_agent_ids),
            channel_type="group",
        )
        await db.update("bus_channels", {"channel_id": channel_id}, {"created_by": marker})
        logger.info(f"[team-room] provisioned {channel_id} for {team_id}")

    current = {m.agent_id for m in await bus.get_channel_members(channel_id)}
    target = set(member_agent_ids)
    for aid in target - current:
        await bus.join_channel(aid, channel_id)
    for aid in current - target:
        await bus.leave_channel(aid, channel_id)

    return channel_id


async def room_roster(db: Any, bus: Any, channel_id: str) -> list[dict]:
    """``[{"agent_id", "name"}]`` for everyone in the room.

    Names come from the `agents` table in one batched read, not a lookup per
    member: a roster is a few dozen rows at most and N+1 here would sit on the
    delivery path.

    Used for @mention resolution — a mention is written as a NAME and has to
    become an id — so a member whose row is missing still appears, keyed by id.
    Dropping them would silently make that teammate unmentionable.
    """
    members = await bus.get_channel_members(channel_id)
    ids = [m.agent_id for m in members]
    if not ids:
        return []
    rows = await db.get_by_ids("agents", "agent_id", ids) or []
    names = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in rows if r}
    return [{"agent_id": aid, "name": names.get(aid, aid)} for aid in ids]


__all__ = [
    "team_room_marker",
    "primary_room_of",
    "get_or_create_team_room",
    "room_roster",
]
