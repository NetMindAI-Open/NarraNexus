"""
@file_name: team_bulletin.py
@author: NarraNexus
@date: 2026-08-10
@description: The team bulletin's business rules — budgets, and what an agent
may write.

Plain functions, not MCP handlers and not route bodies, for the same reason as
`team_files.py`: these rules ARE the feature, and they should be testable
without standing up a tool server or an HTTP client.

They live in the core package rather than beside the REST routes because both
surfaces enforce them — the routes for the user, the MCP tool for an agent —
and a core module reaching into `backend.routes` to borrow them would invert
the layering the architecture depends on.

**Why an agent may write here at all.** The point of the bulletin is that a
thing gets said once. A team that works out its own convention mid-task ("we
settled on report format v2") and cannot record it forces the user back into
the role of scribe for a decision they were not part of.

**Why the authority is asymmetric.** An agent may add, and may retract WHAT IT
ITSELF WROTE. It may never touch a user's entry, another agent's, or the
auto-summary. The bulletin is precisely where the rules constraining an agent
live, so an agent that could delete them could delete its own leash — and it
would not need bad intent to do it, only a plausible-sounding tidy-up. The
user's one-click delete on any entry is the other half of the bargain: write
access is safe to grant because it is trivially revocable.

**Where team_id comes from.** The SERVER-SIDE identity of the turn, never a
tool argument. `team_share_file` takes it from the model and validates three
ways; the stronger option exists here because the turn already knows its team.
Naming a different team is therefore not an attack to catch — it is not
expressible. A private turn has no team identity and so no bulletin to write
to, which is why the empty-team case is refused rather than silently scoped to
something.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
    BULLETIN_MAX_ENTRIES,
    BULLETIN_MAX_ENTRY_CHARS,
    BULLETIN_MAX_TOTAL_CHARS,
    BULLETIN_SOURCE_AGENT,
    BULLETIN_TIER_CURRENT_TASK,
    BULLETIN_TIER_LONG_TERM,
    BulletinUsage,
)

# Marks a bulletin change in the transcript. Same shape as the stop notice
# (``backend/routes/runs.py``): the frontend renders it from an i18n key,
# because the database cannot know the reader's language, and ``content``
# carries an English fallback for consumers that only read text.
BULLETIN_NOTICE_MSG_TYPE = "system_bulletin"

# The owner-stopped-a-run notice. Defined here rather than in
# ``backend/routes/runs.py`` (which now imports it) because core modules need it
# too — the summary worker filters it out of team activity — and a core package
# reaching into a route to borrow a string inverts the layering.
STOP_NOTICE_MSG_TYPE = "system_stop"


# ── budget ──────────────────────────────────────────────────────────────────


class BulletinLimitExceeded(Exception):
    """A write that would breach the bulletin's budget.

    Carries the reason as its message so both callers — the REST route for the
    user, the MCP tool for an agent — hand the same explanation to very
    different readers without re-deriving it.
    """


async def check_bulletin_budget(repo: TeamBulletinRepository, team_id: str) -> BulletinUsage:
    """Current usage of the shared entry budget (summary excluded).

    Exposed so the UI can grey out "add" BEFORE the user types a rule, rather
    than rejecting it after they have written one.
    """
    return await repo.usage(team_id)


async def add_bulletin_entry(
    repo: TeamBulletinRepository,
    *,
    team_id: str,
    content: str,
    source: str,
    author_id: Optional[str],
    tier: str = BULLETIN_TIER_LONG_TERM,
):
    """Add one bulletin entry, or refuse and say why.

    REFUSE, never trim. A silently shortened rule is worse than a rejected one:
    the user is told nothing and goes on believing the whole rule is in force,
    and the half that survived may invert the meaning of the half that did not
    ("never share X with Y" cut at "never share X"). Every refusal names the
    limit it hit, because "too long" without a number leaves the writer — human
    or model — guessing how much to cut.

    The budget is shared between the user and the agents. The prompt does not
    care who wrote a line, so neither does the ceiling; otherwise an agent
    could mint itself room the user cannot have.
    """
    text = (content or "").strip()
    if not text:
        raise BulletinLimitExceeded("A bulletin entry cannot be empty.")
    if len(text) > BULLETIN_MAX_ENTRY_CHARS:
        raise BulletinLimitExceeded(
            f"That entry is {len(text)} characters; the limit is "
            f"{BULLETIN_MAX_ENTRY_CHARS}. Shorten it to the rule itself."
        )

    usage = await repo.usage(team_id)
    if usage.entry_count >= BULLETIN_MAX_ENTRIES:
        raise BulletinLimitExceeded(
            f"The bulletin already holds {usage.entry_count} entries "
            f"(limit {BULLETIN_MAX_ENTRIES}). Remove one before adding another."
        )
    if usage.total_chars + len(text) > BULLETIN_MAX_TOTAL_CHARS:
        raise BulletinLimitExceeded(
            f"The bulletin would reach {usage.total_chars + len(text)} characters "
            f"(limit {BULLETIN_MAX_TOTAL_CHARS}). Shorten or remove an entry first."
        )

    return await repo.add(team_id=team_id, content=text, source=source, author_id=author_id, tier=tier)


async def edit_bulletin_entry(repo: TeamBulletinRepository, *, team_id: str, entry_id: str, content: str):
    """Replace one entry's text, or refuse and say why.

    The projected total SUBTRACTS the text being replaced. Counting the new
    text against a total that still includes the old one would refuse the very
    edit that relieves the budget: a user at the ceiling would be told to free
    space by doing exactly what they were just blocked from doing.

    Returns the entry as it now reads, or None when it is not this team's.
    """
    entry = await repo.get(entry_id)
    # Scoped to the team, not just the id: an entry_id belonging to another of
    # the owner's teams must not be reachable through this one's path.
    if entry is None or entry.team_id != team_id:
        return None

    text = (content or "").strip()
    if not text:
        raise BulletinLimitExceeded("A bulletin entry cannot be empty.")
    if len(text) > BULLETIN_MAX_ENTRY_CHARS:
        raise BulletinLimitExceeded(f"That entry is {len(text)} characters; the limit is {BULLETIN_MAX_ENTRY_CHARS}.")

    usage = await repo.usage(team_id)
    projected = usage.total_chars - len(entry.content) + len(text)
    if projected > BULLETIN_MAX_TOTAL_CHARS:
        raise BulletinLimitExceeded(
            f"The bulletin would reach {projected} characters (limit {BULLETIN_MAX_TOTAL_CHARS}). Shorten it further."
        )

    await repo.update_content(entry_id, text)
    entry.content = text
    return entry


async def post_bulletin_notice(db, team_id: str, action: str, actor: str = "") -> None:
    """Leave a trace in the room when the bulletin changes.

    The bulletin governs every reply from now on, so a silent change means
    members start behaving differently with nothing in the transcript to explain
    why — the same reasoning that makes a stopped run post a notice.

    Lives here rather than beside the REST routes because BOTH writers need it.
    The first version was route-only, so a rule an AGENT pinned changed how the
    whole team behaved and left no mark at all — the acceptance criterion says
    "a system message appears when the bulletin updates", and half the writers
    were skipping it.

    NO MENTIONS, deliberately. A mention would wake every member the instant a
    rule is written, and a rule written BY an agent would then wake the agents
    that might write another — the write-wake-write loop. The notice is for the
    humans reading the room; agents pick the rules up on their next turn
    regardless, which is the entire point of the bulletin.

    Best-effort: a write that succeeded must not report failure because its
    announcement did not land.
    """
    try:
        from narranexus.platform.message_bus.local_bus import LocalMessageBus
        from narranexus.platform.message_bus.team_rooms import primary_room_of

        channel_id = await primary_room_of(db, team_id)
        # No room means nobody to notify. Creating one here would be the tail
        # wagging the dog: a bulletin edit should not conjure a chat channel.
        if not channel_id:
            return
        team = await db.get_one("teams", {"team_id": team_id})
        sender = actor or f"{USER_SENDER_PREFIX}{(team or {}).get('owner_user_id', '')}"
        bus = LocalMessageBus(backend=db._backend)
        await bus.send_message(
            from_agent=sender,
            to_channel=channel_id,
            content=f"Team bulletin {action}.",
            msg_type=BULLETIN_NOTICE_MSG_TYPE,
            mentions=None,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[team-bulletin] could not post notice for {team_id}: {e}")


# ── the agent-facing surface ────────────────────────────────────────────────


async def _membership_error(db, agent_id: str, team_id: str) -> Optional[str]:
    """None when this agent may act on this team, else the reason it may not.

    Membership, not ownership. One user owns many teams, so "belongs to the
    owner of this team" is not permission to write to it — that check would let
    an agent in team A pin rules into team B.
    """
    if not team_id:
        return "You are not in a team conversation right now, so there is no team bulletin to write to."
    agent_row = await db.get_one("agents", {"agent_id": agent_id})
    if not agent_row:
        return "unknown agent"
    team = await db.get_one("teams", {"team_id": team_id})
    if not team or team.get("owner_user_id") != agent_row.get("created_by"):
        return "team not found for this owner"
    membership = await db.get_one("team_members", {"team_id": team_id, "agent_id": agent_id})
    if not membership:
        return "you are not a member of this team"
    return None


async def post_team_bulletin(
    *,
    db: Any,
    agent_id: str,
    team_id: str,
    content: str,
    tier: str = BULLETIN_TIER_LONG_TERM,
) -> Dict[str, Any]:
    """Pin a rule the team has agreed on.

    Budget failures return the same explanation the user gets, numbers
    included, so the agent can shorten and retry instead of looping against an
    opaque refusal.
    """
    reason = await _membership_error(db, agent_id, team_id)
    if reason:
        return {"success": False, "error": reason}

    normalized = BULLETIN_TIER_CURRENT_TASK if tier == BULLETIN_TIER_CURRENT_TASK else BULLETIN_TIER_LONG_TERM
    try:
        entry = await add_bulletin_entry(
            TeamBulletinRepository(db),
            team_id=team_id,
            content=content,
            source=BULLETIN_SOURCE_AGENT,
            author_id=agent_id,
            tier=normalized,
        )
    except BulletinLimitExceeded as e:
        return {"success": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[team-bulletin] agent write failed ({agent_id}/{team_id}): {e}")
        return {"success": False, "error": str(e)}

    await post_bulletin_notice(db, team_id, "updated", actor=agent_id)
    return {"success": True, "entry_id": entry.entry_id, "content": entry.content}


async def remove_team_bulletin(*, db: Any, agent_id: str, team_id: str, entry_id: str) -> Dict[str, Any]:
    """Retract an entry THIS agent wrote. Anything else is refused.

    The three refusals are one rule seen from three sides: an agent may undo
    its own contribution and nobody else's. A user's entry is off limits
    because it is the constraint; another agent's because teammates are not
    interchangeable; the summary because nobody authored it.
    """
    reason = await _membership_error(db, agent_id, team_id)
    if reason:
        return {"success": False, "error": reason}

    repo = TeamBulletinRepository(db)
    entry = await repo.get(entry_id)
    # Team-scoped, so an entry_id from elsewhere is not a key that opens this
    # door — the turn's team bounds the write, not the id's existence.
    if entry is None or entry.team_id != team_id:
        return {"success": False, "error": "bulletin entry not found"}
    if entry.source != BULLETIN_SOURCE_AGENT or entry.author_id != agent_id:
        return {
            "success": False,
            "error": ("you can only remove bulletin entries you wrote yourself. Ask the user to remove this one."),
        }

    await repo.delete(entry_id)
    await post_bulletin_notice(db, team_id, "updated", actor=agent_id)
    return {"success": True, "entry_id": entry_id}
