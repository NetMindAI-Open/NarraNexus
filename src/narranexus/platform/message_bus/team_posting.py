"""
@file_name: team_posting.py
@author:
@date: 2026-08-17
@description: Putting an agent's words into a team room — the one path.

A team reply used to be the agent's PLAIN TEXT, auto-posted by
``MessageBusTrigger._deliver_reply``. That made the team room the only surface
in the system where "plain text reaches nobody" was false, and that exception
propagated: the framework constitution, ChatModule's instructions and the bus
module's rules all assert the general rule, and only one of the three had a way
to be switched off per turn. Six review rounds on PR #311 were spent on the
contradictions that grew out of it.

So the room now takes a tool call like every other surface, and this module is
what that tool calls. Everything ``_deliver_reply`` did lives here — @mention
resolution, the agent-to-agent hop cap, the turn stamp — because those are
properties of POSTING INTO A ROOM, not of the trigger that happened to own the
old delivery path.

The cap moving here is the fix for a specific hole: it was implemented only in
``_deliver_reply``, while the old ``bus_send_message`` could write into a team
room with no hop counting at all. The loop-breaker was installed on the door the
agent was told not to use.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    placeholders as _platform_placeholders,
)
from narranexus.platform.schema.team_schema import USER_SENDER_PREFIX

#: Default number of consecutive agent hops that may pass with no human message
#: before @mentions stop being delivered. A dead flow IS a long unbroken run of
#: agent messages, so this is the number that decides when the room stops
#: relaying. 4 (the original) was too small: an ordinary team task needs more
#: than four uninterrupted agent exchanges to finish, so the room kept stalling
#: and waiting for a human. It is still a FINITE loop-breaker — a runaway
#: agent-to-agent @storm is a real incident class — so overrides that are
#: non-positive or unparseable fall back to the default rather than disabling it.
_DEFAULT_MAX_TEAM_AGENT_HOPS = 30
_MAX_HOPS_ENV = "TEAM_MAX_AGENT_HOPS"
#: Hard upper bound on the resolved cap. The value feeds a `LIMIT` on
#: `bus_messages` (the bus's busiest table) on every team post, so a fat-finger
#: `TEAM_MAX_AGENT_HOPS=100000` would turn a few-row scan into a six-figure one
#: and read as a global slowdown, not an error. Clamp it.
_MAX_TEAM_AGENT_HOPS_CEILING = 500


def _resolve_hop_cap() -> int:
    """Resolve the agent-to-agent hop cap, honoring the env override.

    A blank, unparseable, or non-positive ``TEAM_MAX_AGENT_HOPS`` falls back to
    the default: the cap is a loop-breaker and must never be silently disabled.
    A value above the ceiling is clamped (it lands in a SQL ``LIMIT``).
    """
    raw = os.environ.get(_MAX_HOPS_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_TEAM_AGENT_HOPS
    try:
        val = int(raw.strip())
    except ValueError:
        logger.warning(
            "[team-post] ignoring unparseable %s=%r; using default %d",
            _MAX_HOPS_ENV, raw, _DEFAULT_MAX_TEAM_AGENT_HOPS,
        )
        return _DEFAULT_MAX_TEAM_AGENT_HOPS
    if val <= 0:
        logger.warning(
            "[team-post] ignoring non-positive %s=%r; the hop cap is a "
            "loop-breaker and cannot be disabled; using default %d",
            _MAX_HOPS_ENV, raw, _DEFAULT_MAX_TEAM_AGENT_HOPS,
        )
        return _DEFAULT_MAX_TEAM_AGENT_HOPS
    if val > _MAX_TEAM_AGENT_HOPS_CEILING:
        logger.warning(
            "[team-post] clamping %s=%d to ceiling %d (it feeds a SQL LIMIT "
            "on bus_messages)",
            _MAX_HOPS_ENV, val, _MAX_TEAM_AGENT_HOPS_CEILING,
        )
        return _MAX_TEAM_AGENT_HOPS_CEILING
    return val


MAX_TEAM_AGENT_HOPS = _resolve_hop_cap()


#: One @mention token as `extract_team_mentions` reads it. Shared with the
#: roster's `mention_token`, so the token the prompt shows is one the parser
#: takes. In a str pattern `\w` is Unicode-aware and already matches CJK
#: ideographs (and every other letter or digit), so no explicit range is
#: needed. The frontend `mentionPattern.ts` keeps its explicit CJK range
#: because a JS `\w` is ASCII-only; the backend set is a superset of it.
_MENTION_TOKEN = re.compile(r"\w+")


def extract_team_mentions(text: str, member_map: Dict[str, str]) -> List[str]:
    """Resolve @mentions in a reply to channel-member agent ids.

    Returns EITHER ``["@everyone"]`` OR a list of ids, never a mix — callers
    downstream (the cap notice in particular) depend on that shape.
    """
    tokens = {t.lower() for t in re.findall(rf"@({_MENTION_TOKEN.pattern})", text or "")}
    if not tokens:
        return []
    if "all" in tokens or "everyone" in tokens:
        return ["@everyone"]
    out: List[str] = []
    for aid, name in member_map.items():
        nm = (name or aid).lower()
        first = nm.split()[0] if nm.split() else nm
        if nm in tokens or first in tokens or any(
            len(t) >= 2 and nm.startswith(t) for t in tokens
        ):
            out.append(aid)
    return out


def mention_token(agent_id: str, member_map: Dict[str, str]) -> Optional[str]:
    """The exact bare token that, written after ``@``, wakes this member and no
    one else — or None when the name offers none (it starts with a character a
    token cannot hold, or its token also wakes another member).

    The roster prints names as quoted literals (see `inline_field`), while the
    parser reads only a bare ``@word``; an agent copying the quoted form would
    write ``@"Ana"``, which resolves to nobody, silently. So the prompt shows
    the token itself, and the token is checked against the real parser rather
    than a copy of its rules."""
    name = (member_map.get(agent_id) or agent_id).strip()
    m = _MENTION_TOKEN.match(name)
    if not m:
        return None
    token = m.group(0)
    if extract_team_mentions(f"@{token}", member_map) != [agent_id]:
        return None
    return token


async def team_cascade_depth(db: Any, channel_id: str) -> int:
    """How many consecutive agent (non-user) messages end this channel.

    A user message resets it. Platform lines are excluded IN SQL, not skipped
    afterwards: the window is a fixed LIMIT, so a skipped row still consumes a
    slot — with three patrol lines among the last six messages only three
    countable hops fit, ``depth`` could never reach the cap, and the runaway-@
    guard silently stopped applying in exactly the rooms patrol frequents.
    """
    # `%s` and the CLIENT, not the raw backend's own placeholder.
    #
    # This query came out of `MessageBusTrigger`, where `self._bus._db` IS the raw
    # backend and takes SQL verbatim. Every caller here holds an
    # `AsyncDatabaseClient` instead — which has no `.placeholder` at all, so
    # reading one raised `AttributeError` inside the cap check and took the whole
    # send down with it: the hop cap turned every `message_team` call into
    # `{"success": false}`. The client translates `%s` per dialect, which is what
    # the dual-dialect contract wants anyway.
    rows = await db.execute(
        f"SELECT from_agent FROM bus_messages WHERE channel_id = %s "
        f"AND (msg_type IS NULL OR msg_type NOT IN ({_platform_placeholders()})) "
        f"ORDER BY created_at DESC LIMIT {MAX_TEAM_AGENT_HOPS + 2}",
        (channel_id, *PLATFORM_MSG_TYPES),
        fetch=True,
    )
    depth = 0
    for r in rows or []:
        if str(r["from_agent"]).startswith(USER_SENDER_PREFIX):
            break
        depth += 1
    return depth


async def _record_errands(
    db: Any,
    *,
    team_id: str,
    channel_id: str,
    agent_id: str,
    mentions: Optional[Sequence[str]],
    text: str,
    message_id: str,
    root_run_id: str,
) -> None:
    """Settle and open the room's message-level errands for one landed post.

    A named seam rather than an inline block, and that is the point: two
    independent safeties protect this call and each needs its own test.

    1. **Position** — it runs on the far side of `bus.send_message`, never
       wrapped together with it. Inside, a raising hook would make a post that
       IS in the room report itself as failed, and the room grows a "could not
       deliver this" notice underneath a reply sitting right there (the
       accident #302's comments record).
    2. **The swallow** — it never raises, so production never reaches the
       failure mode position protects against.

    Folded into one `try` inside `post_team_reply`, the two are the same
    construct and neither can be tested without the other: patching the errand
    functions is caught by the swallow, so a call that had drifted above the
    post would still look green. Patching THIS name bypasses the swallow and
    leaves only position under test — which is why it has a name.

    It rides in `team_posting` rather than the trigger for the same reason the
    hop cap does: it is a property of putting a message into a team room, not
    of the loop that happened to trigger the turn. The trigger owned it while
    the trigger owned posting; posting is a tool call now, and a book-keeping
    step left behind would simply stop running (PR #310's hand-off board,
    silently empty).

    Ordering is the whole design: close first, then open. On a founding message
    ("收到…完成后交付 @A4") the close is a no-op — it is a promise, not a
    delivery — so the sender's own errand stays watched. Whether the open adds
    the next link depends on WHO sent it since 2026-09-03: the lead's hand-on
    books @A4, a member's does not (`errand.opens_handoffs`). Reversing the
    order would let a hand-off close the errand it just created.

    `mentions` is the POST-cap list: an @mention the cascade cap stripped never
    reached the teammate, so an errand for it would be owed by someone who was
    never asked.

    **Never raises.** The reply is already in the room and the hop has
    succeeded; a board write that could fail that hop would trade a working
    delivery for bookkeeping. The cost is bounded and self-correcting: a missed
    close leaves an item patrol asks about once, a missed open leaves the room
    exactly as it was before this layer existed.
    """
    if not team_id:  # errands are a team-room fact; ordinary bus DMs have none
        return
    try:
        from narranexus.platform.message_bus.errand import (
            close_delivered_errands,
            record_handoffs,
        )

        await close_delivered_errands(
            db,
            team_id=team_id,
            channel_id=channel_id,
            agent_id=agent_id,
            text=text,
        )
        # Who may hand work down is a fact about the TEAM (its lead), not
        # about the message; one lookup here rather than a roster field the
        # tool would have to remember to pass. Sits AFTER the close on
        # purpose: if this lookup fails, only the open is lost — and for a
        # member's post the open is a no-op anyway — whereas a skipped close
        # would leave a delivered errand on the board for patrol to chase.
        team = await db.get_one("teams", {"team_id": team_id})
        lead_agent_id = (team or {}).get("lead_agent_id") or None
        await record_handoffs(
            db,
            team_id=team_id,
            channel_id=channel_id,
            from_agent=agent_id,
            mentions=mentions,
            text=text,
            message_id=message_id,
            root_run_id=root_run_id,
            lead_agent_id=lead_agent_id,
        )
    except Exception as e:  # noqa: BLE001 — see "Never raises" above
        logger.warning(
            f"[errand] bookkeeping failed team={team_id} agent={agent_id}: "
            f"{type(e).__name__}: {e}"
        )


async def post_team_reply(
    *,
    db: Any,
    bus: Any,
    agent_id: str,
    team_id: str,
    channel_id: str,
    text: str,
    roster: Sequence[dict],
    event_id: Optional[str] = None,
    root_run_id: str = "",
) -> dict:
    """Post one agent message into a team room, applying the room's rules.

    Returns ``{"message_id", "mentioned", "capped"}``. ``capped`` names who was
    NOT pulled in when the hop cap fired, so the caller can narrate it — a
    platform that silently declines to relay trains the room to read it as the
    teammate ignoring the request.
    """
    if not (text or "").strip():
        # Belt, behind the tool's own guard. Raising rather than returning a
        # shape because production cannot reach it — `message_team` refuses blank
        # text before calling — so anything that does get here is a caller that
        # skipped the tool, i.e. a test. `_team_turn.speak_in_room` is exactly
        # such a caller, so a guard placed only in the tool would leave every
        # team test able to post nothing and call it a delivery.
        raise ValueError("post_team_reply: refusing to post empty text")

    member_map = {r["agent_id"]: r.get("name") or r["agent_id"] for r in roster}
    mentions = extract_team_mentions(text, member_map)
    capped: dict = {"names": [], "everyone": False}

    if mentions:
        depth = await team_cascade_depth(db, channel_id)
        if depth >= MAX_TEAM_AGENT_HOPS:
            logger.info(
                f"[team-post] cascade depth {depth} >= {MAX_TEAM_AGENT_HOPS} "
                f"in {channel_id}; dropping @mentions to break the loop"
            )
            capped["everyone"] = "@everyone" in mentions
            capped["names"] = [
                member_map.get(m, m) for m in mentions if m != "@everyone"
            ]
            mentions = []

    message_id = await bus.send_message(
        from_agent=agent_id,
        to_channel=channel_id,
        content=text,
        mentions=mentions or None,
        event_id=event_id,
        root_run_id=root_run_id,
    )

    await _record_errands(
        db,
        team_id=team_id,
        channel_id=channel_id,
        agent_id=agent_id,
        mentions=mentions,
        text=text,
        message_id=message_id,
        root_run_id=root_run_id,
    )

    if capped["names"] or capped["everyone"]:
        # Best-effort: the reply is already in the room and failing to narrate
        # the cap must not undo that.
        try:
            from narranexus.platform.message_bus.team_notices import post_cascade_capped

            await post_cascade_capped(
                db,
                team_id=team_id,
                channel_id=channel_id,
                dropped=capped["names"],
                depth=MAX_TEAM_AGENT_HOPS,
                dropped_everyone=capped["everyone"],
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[team-post] could not narrate the cap: {e}")

    return {
        "message_id": message_id,
        "mentioned": mentions,
        "capped": capped,
    }


__all__ = [
    "MAX_TEAM_AGENT_HOPS",
    "extract_team_mentions",
    "mention_token",
    "team_cascade_depth",
    "post_team_reply",
]
