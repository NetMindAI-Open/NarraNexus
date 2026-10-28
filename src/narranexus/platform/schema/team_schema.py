"""
@file_name: team_schema.py
@author: NetMind.AI
@date: 2026-05-08
@description: Team & TeamMember Pydantic models

Subproject 1: Team Membership
- Team: a named group of agents
- TeamMember: many-to-many between teams and agents

Source values for Team.source:
- "user": created by the user via UI
- "agent": created by an agent via the `create_team` MCP tool
- "bundle:<bundle_id>": auto-created from a bundle import (subproject 2)
"""

from typing import Any, Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


# The synthetic markers a team room is built on. Defined with Team rather than
# inside any one consumer: six modules construct or match them (the bus trigger,
# the teams route, the runs route, the bulletin notice, the work-board tools,
# the summary worker), and each had been retyping the literal.
#
# A team channel's ``created_by`` is this prefix + team_id, which does two jobs:
# it identifies the room deterministically without an extra column, and it makes
# the owner a NON-AGENT — MessageBusTrigger always activates a channel's owner,
# so a member in that seat would answer everything. Delivery in a team room is
# purely @-mention driven, and that property rests on this marker.
TEAM_ROOM_OWNER_PREFIX = "team_"

# The user posts into a team room as this prefix + user_id — also a non-agent
# sender, which is how the transcript tells a person from a teammate.
USER_SENDER_PREFIX = "usr_"


class Team(BaseModel):
    id: Optional[int] = None
    team_id: str
    owner_user_id: str
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    source: str = "user"
    intro_md: Optional[str] = None
    # Agent that answers a team-chat message with no @mention (None = earliest member).
    lead_agent_id: Optional[str] = None
    # Leader patrol. Read-only here on purpose: `patrol.py` and the patrol
    # toggle own these columns, and routing them through the team CRUD's
    # _entity_to_row would let an unrelated team edit reset the cursor.
    # None on patrol_enabled = undecided, which reads as ON for a team that
    # HAS a lead (setting a lead IS the act of saying "this one is responsible").
    patrol_enabled: Optional[bool] = None
    last_patrol_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class TeamMember(BaseModel):
    id: Optional[int] = None
    team_id: str
    agent_id: str
    joined_at: Optional[datetime] = None


# ===== Bulletin =====
#
# The team's standing rules, loaded into EVERY member's EVERY team turn. See
# `repository/team_bulletin_repository.py` for why the auto-summary is a slot
# rather than a growing list, and why it has a budget of its own.

BULLETIN_SOURCE_USER = "user"
BULLETIN_SOURCE_AGENT = "agent"
BULLETIN_SOURCE_SUMMARY = "auto_summary"

BULLETIN_TIER_LONG_TERM = "long_term"
BULLETIN_TIER_CURRENT_TASK = "current_task"

# Budgets. Anything that reaches a prompt needs a ceiling; these are refused at
# the edge with an explanation rather than trimmed, because a user who is told
# nothing assumes the whole rule is in force.
BULLETIN_MAX_ENTRIES = 20
BULLETIN_MAX_ENTRY_CHARS = 500
BULLETIN_MAX_TOTAL_CHARS = 2000
# Separate on purpose: automatic output must never be able to push a
# hand-written rule out of the prompt.
BULLETIN_MAX_SUMMARY_CHARS = 800


class BulletinEntry(BaseModel):
    id: Optional[int] = None
    entry_id: str
    team_id: str
    content: str
    source: str = BULLETIN_SOURCE_USER
    author_id: Optional[str] = None
    tier: str = BULLETIN_TIER_LONG_TERM
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BulletinUsage(BaseModel):
    """What the shared entry budget currently holds. Excludes the summary."""

    entry_count: int = 0
    total_chars: int = 0


# ===== API request / response =====


class CreateTeamRequest(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = None


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    intro_md: Optional[str] = None
    lead_agent_id: Optional[str] = None


class AddMemberRequest(BaseModel):
    agent_id: str


class TeamWithMembers(BaseModel):
    """A team as the sidebar needs it: who is in it, and whether it has spoken.

    The activity fields answer a question a team row could not answer before:
    a room the user left looked identical whether six agents had been talking for
    ten minutes or nothing had happened. They are the server half of the unread
    mark — the read watermark itself is client-side, per device.

    All three are None for a room that does not exist yet or has said nothing the
    user has not already seen themselves (their own messages and the platform's
    own notices do not qualify; see `_team_room_activity`).
    """

    team: Team
    member_agent_ids: List[str] = Field(default_factory=list)
    last_message_at: Optional[str] = None
    last_message_preview: Optional[str] = None
    last_message_author: Optional[str] = None


class TeamListResponse(BaseModel):
    teams: List[TeamWithMembers]


class TeamOperationResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    team: Optional[Team] = None

def resolve_default_responder(
    lead_agent_id: Optional[str], member_agent_ids: List[str]
) -> Optional[str]:
    """The member a team falls back to when nobody is named.

    ``lead_agent_id`` if it is set and still a member, else the earliest-joined
    one (``member_agent_ids`` is ordered by join time). None for an empty team,
    so callers have to decide what "no members" means for them rather than
    getting a plausible-looking empty string.

    Lives with Team for the same reason `patrol_is_on` does: it is a rule ABOUT
    Team, and it spent a while inside `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py`, where the
    team-summary worker could not reach it — so the worker grew a second copy,
    which is how one rule becomes two that drift.

    Two callers today: "who answers a message with no @mention" (the route) and
    "whose token budget a team summary is recorded against" (the worker). They
    want the same answer because they are asking the same question — which
    member does this team treat as its default.

    Takes the lead id, not a Team: one caller holds a model and the other a raw
    row, and sniffing which one it got put a type check inside a rule that has
    nothing to do with types. The callers know what they are holding.
    """
    if not member_agent_ids:
        return None
    if lead_agent_id and lead_agent_id in member_agent_ids:
        return lead_agent_id
    return member_agent_ids[0]


# `patrol_enabled` and `lead_agent_id` are both fields of Team, so the rule that
# combines them lives with Team. It sat in `team_work_schema.py` for one release
# because patrol was the only caller; that put a Team rule in the work-item
# module, where nobody looking at Team would find it.
def patrol_is_on(team: Any) -> bool:
    """Whether the Leader's periodic sweep is active for this team.

    THE single implementation of a rule that is easy to state and easy to get
    subtly wrong: ``patrol_enabled`` is NULL for every team that predates the
    column, and NULL reads as ON — but only for a team that HAS a lead, because
    setting a lead is the act of naming someone responsible. A team with no
    lead never patrols; the platform does not appoint one.

    Accepts either a ``Team`` model or a raw row dict, since the route layer
    holds the former and the trigger the latter.
    """
    get = team.get if isinstance(team, dict) else lambda k, d=None: getattr(team, k, d)
    if not (get("lead_agent_id") or ""):
        return False
    raw = get("patrol_enabled")
    return True if raw is None else bool(raw)
