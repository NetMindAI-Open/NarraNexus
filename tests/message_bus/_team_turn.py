"""
@file_name: _team_turn.py
@date: 2026-08-17
@description: One place that fakes "the agent spoke in the room".

Before 2026-08-17 a team reply was the agent's PLAIN TEXT and the trigger posted
it, so every team-turn test stubbed `_invoke_runtime` and called the
`on_plain_text_delivery` callback the trigger handed in. That callback is gone —
the room is a tool call (`message_team`) now — so a stub that skips it would be
asserting about a path production no longer takes.

What replaces it has to go through `team_posting.post_team_reply`, because that
is what the tool calls and where @mention resolution, the hop cap and the cap's
narration live. A stub that inserted a `bus_messages` row directly would pass
while none of that ran.

Kept in one module rather than copied into a dozen test files: the shape of "the
agent spoke" is exactly the kind of thing that drifts per-file and then a
contract change like this one has to be made a dozen times.
"""
from __future__ import annotations

from typing import Optional

from narranexus.platform.message_bus.team_posting import post_team_reply


async def speak_in_room(
    *,
    db,
    bus,
    agent_id: str,
    team_id: str,
    channel_id: str,
    text: str,
    event_id: Optional[str] = None,
    root_run_id: str = "",
) -> dict:
    """Post as the agent would through `message_team`, roster included.

    The roster is resolved the same way the tool resolves it — channel members
    plus their display names — because @mention matching is done on NAMES, so a
    stub with an empty roster silently disables mention handling and every
    cascade assertion built on it.
    """
    members = await bus.get_channel_members(channel_id)
    ids = [m.agent_id for m in members]
    rows = await db.get_by_ids("agents", "agent_id", ids) if ids else []
    names = {r["agent_id"]: (r.get("agent_name") or r["agent_id"])
             for r in (rows or []) if r}
    roster = [{"agent_id": a, "name": names.get(a, a)} for a in ids]
    return await post_team_reply(
        db=db, bus=bus, agent_id=agent_id, team_id=team_id,
        channel_id=channel_id, text=text, roster=roster,
        event_id=event_id, root_run_id=root_run_id,
    )
