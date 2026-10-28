"""
@file_name: team_notices.py
@author: NarraNexus
@date: 2026-08-12
@description: Things the platform did to a team room, said out loud in it.

A team room was doing several things the room never mentioned. Each one is
correct behaviour that looked, from inside the room, like something else:

**The cascade cap.** When agent hops pile up since the last human message, the
platform stops propagating @mentions so two agents cannot loop forever. That
guard is right. It was recorded in a server log — so from the room it read as
"the agent asked a teammate for help and the teammate ignored it". The user
asked for someone to be pulled in, the platform declined, and nobody said so.

**Roster and lead changes.** Who is in the room decides what `@all` reaches and
who can answer; the lead decides who answers when nobody is named and who patrol
wakes. Both changed silently, which also leaves the transcript above the change
reading as though the current roster wrote it.

Every notice here follows the shape `system_stop` established and the bulletin
notice reused, for reasons that are the same each time:

  * `msg_type` marks it, so the frontend renders a centred line rather than a
    bubble with an identity colour;
  * ``content`` carries an English fallback, because the database cannot know
    the reader's language but text-only consumers still read this;
  * **NO MENTIONS.** For the cascade notice this is load-bearing rather than
    tidy: it exists precisely because the chain must stop, so waking anyone
    would restart the loop it was posted to break. For a roster change, adding a
    member should not immediately hand them a turn;
  * registered in `system_messages.PLATFORM_MSG_TYPES`, so it is excluded from
    the summary trigger, from the transcript's speaker rendering, and from the
    agent-hop count — a cap notice that counted as a hop would make the cap
    tighten every time it fired.

Best-effort throughout: the turn that hit the cap has already produced a real
reply, and failing to narrate it must not lose that.
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

CASCADE_MSG_TYPE = "system_cascade"
ROSTER_MSG_TYPE = "system_roster"


async def _post(db, team_id: str, channel_id: str, content: str, msg_type: str) -> None:
    """Post one platform line into a team room, or quietly do nothing.

    Resolves the sender from the team's own marker rather than any agent: this is
    the room speaking. A missing room or team means there is nobody to tell.
    """
    try:
        from narranexus.platform.message_bus.local_bus import LocalMessageBus
        from narranexus.platform.schema.team_schema import TEAM_ROOM_OWNER_PREFIX

        channel = await db.get_one("bus_channels", {"channel_id": channel_id})
        if not channel:
            return
        bus = LocalMessageBus(backend=db._backend)
        await bus.send_message(
            from_agent=f"{TEAM_ROOM_OWNER_PREFIX}{team_id}",
            to_channel=channel_id,
            content=content,
            msg_type=msg_type,
            mentions=None,
        )
    except Exception as e:  # noqa: BLE001 — narrating must never cost the turn
        logger.warning(f"[team-notice] could not post {msg_type} in {channel_id}: {e}")


async def post_cascade_capped(
    db,
    *,
    team_id: str,
    channel_id: str,
    dropped: List[str],
    depth: int,
    dropped_everyone: bool = False,
) -> None:
    """Say that @mentions were not delivered because the hop cap was reached.

    Names WHO was not reached. "A limit was hit" is not actionable; "Bruno was
    not pulled in" tells the user exactly who to @ themselves, which is the
    entire point of telling them at all.

    ``dropped_everyone`` is a separate flag rather than ``"@everyone"`` sitting
    in ``dropped``, because the two are different KINDS of fact.
    ``_extract_team_mentions`` returns either ``["@everyone"]`` or a list of
    agent ids and never a mix, so an `@all` that hits the cap leaves ``dropped``
    empty — which used to return early and say nothing at all, in the single
    most common way a six-member room reaches the cap. And "@everyone was not
    pulled in" would be a sentence about a person who does not exist; what was
    not pulled in is the rest of the team.

    Silent only when nothing was dropped in either sense — announcing a cap that
    did not fire trains the reader to ignore the line.
    """
    if not dropped and not dropped_everyone:
        return
    limit = (
        f"Reached the {depth}-hop limit for agent-to-agent mentions; "
    )
    if dropped_everyone:
        body = "the rest of the team was not pulled in."
    else:
        # "Ana, Bruno was not pulled in" is not a sentence, and this line exists
        # to be read by a person who then has to act on it.
        names = ", ".join(dropped)
        verb = "were" if len(dropped) > 1 else "was"
        body = f"{names} {verb} not pulled in."
    await _post(
        db,
        team_id,
        channel_id,
        f"{limit}{body} @mention them yourself to continue.",
        CASCADE_MSG_TYPE,
    )


async def post_roster_change(
    db,
    *,
    team_id: str,
    channel_id: str,
    action: str,
    agent_name: str,
) -> None:
    """Say that the room's membership or its lead changed.

    ``action`` is "joined" | "left" | "lead".
    """
    text = {
        "joined": f"{agent_name} joined the team.",
        "left": f"{agent_name} left the team.",
        "lead": f"{agent_name} is now the team's Leader.",
    }.get(action)
    if not text:
        logger.warning(f"[team-notice] unknown roster action {action!r}")
        return
    await _post(db, team_id, channel_id, text, ROSTER_MSG_TYPE)
