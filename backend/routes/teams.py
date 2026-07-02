"""
@file_name: teams.py
@author: NetMind.AI
@date: 2026-05-08
@description: REST API for team membership management

Subproject 1: Team Membership

Endpoints (all under /api/teams):
- GET    /                       List teams owned by current user
- POST   /                       Create a team
- GET    /{team_id}              Get one team with members
- PATCH  /{team_id}              Update team metadata
- DELETE /{team_id}              Delete team (members are unlinked, agents kept)
- POST   /{team_id}/members      Add agent to team
- DELETE /{team_id}/members/{agent_id}  Remove agent from team
"""

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.repository import TeamRepository, TeamMemberRepository
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.schema.team_schema import (
    CreateTeamRequest,
    UpdateTeamRequest,
    AddMemberRequest,
    TeamWithMembers,
    TeamListResponse,
    TeamOperationResponse,
)
from backend.auth import resolve_current_user_id


router = APIRouter()


async def _user_id_for_request(request: Request) -> str:
    # Unified across cloud (JWT) and local (X-User-Id header) modes —
    # auth_middleware populates request.state.user_id either way, so
    # downstream filtering is identical. See backend/auth.py for the
    # mode-specific identity source.
    return await resolve_current_user_id(request)


# --- Team group chat (over the message bus) --------------------------------
#
# A team's group chat is a single message-bus group channel whose
# ``created_by`` is the synthetic marker ``team_<team_id>`` (NOT an agent), so:
#   * the channel is found deterministically (no extra schema/column), and
#   * no member agent is the "channel owner", which in MessageBusTrigger is
#     always activated by any message — here delivery is purely @-mention
#     driven (the user @mentions agents; @all maps to the bus "@everyone").
# The user posts as the synthetic sender ``usr_<user_id>``. The standalone
# MessageBusTrigger picks the message up and runs the @mentioned agents; their
# replies post back into the same channel (see message_bus_trigger.py).

TEAM_ROOM_OWNER_PREFIX = "team_"
USER_SENDER_PREFIX = "usr_"


class TeamChatSendRequest(BaseModel):
    """User message into a team group chat. ``mentions`` carries agent_ids
    and/or the literal ``"@all"`` (mapped to the bus "@everyone")."""

    content: str
    mentions: list[str] = []


async def _get_or_create_team_room(db, bus: LocalMessageBus, team_id: str, team_name: str, member_agent_ids: list[str]) -> str:
    """Find (or create) the team's group-chat channel and sync its members to
    the team's current agents. Returns the channel_id."""
    marker = f"{TEAM_ROOM_OWNER_PREFIX}{team_id}"
    existing = await db.get_one("bus_channels", {"created_by": marker, "channel_type": "group"})
    if existing:
        channel_id = existing["channel_id"]
    else:
        # create_channel sets created_by = members[0]; immediately rewrite it to
        # the non-agent marker so no member is the always-activated owner.
        channel_id = await bus.create_channel(
            name=team_name or "Team",
            members=list(member_agent_ids),
            channel_type="group",
        )
        await db.update("bus_channels", {"channel_id": channel_id}, {"created_by": marker})

    # Sync membership to the team's current agents (add missing, drop extras).
    current = {m.agent_id for m in await bus.get_channel_members(channel_id)}
    target = set(member_agent_ids)
    for aid in target - current:
        await bus.join_channel(aid, channel_id)
    for aid in current - target:
        await bus.leave_channel(aid, channel_id)

    return channel_id


@router.post("/{team_id}/chat/messages")
async def send_team_chat(team_id: str, payload: TeamChatSendRequest, request: Request):
    user_id = await _user_id_for_request(request)
    if not (payload.content or "").strip():
        raise HTTPException(status_code=400, detail="Message content is required")

    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    members = await member_repo.list_members_by_team(team_id)
    bus = LocalMessageBus(backend=db._backend)
    channel_id = await _get_or_create_team_room(db, bus, team_id, team.name, members)

    # Map the UI's "@all" to the bus-native "@everyone"; pass agent_ids through.
    resolved = ["@everyone" if m == "@all" else m for m in (payload.mentions or [])]
    msg_id = await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}{user_id}",
        to_channel=channel_id,
        content=payload.content.strip(),
        mentions=resolved or None,
    )
    logger.info(f"Team chat: user {user_id} -> team {team_id} channel {channel_id} (mentions={resolved})")
    return {"success": True, "message_id": msg_id, "channel_id": channel_id}


@router.get("/{team_id}/chat/messages")
async def get_team_chat(team_id: str, request: Request, since: str | None = None):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    members = await member_repo.list_members_by_team(team_id)
    bus = LocalMessageBus(backend=db._backend)
    channel_id = await _get_or_create_team_room(db, bus, team_id, team.name, members)

    messages = await bus.get_messages(channel_id, since=since, limit=200)

    # Resolve sender display names: agents -> agent_name; usr_<id> -> the user.
    agent_rows = await db.get_by_ids("agents", "agent_id", members) if members else []
    name_by_agent = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in agent_rows if r}
    user_name = await UserRepository(db).get_display_name(user_id)

    out = []
    for m in messages:
        is_user = m.from_agent.startswith(USER_SENDER_PREFIX)
        out.append({
            "message_id": m.message_id,
            "from_agent": m.from_agent,
            "author_name": (user_name or "You") if is_user else name_by_agent.get(m.from_agent, m.from_agent),
            "is_user": is_user,
            "content": m.content,
            "created_at": m.created_at,
        })

    # "Thinking" members: those with an unprocessed message that @mentions them
    # (or @everyone) in this room — i.e. the trigger is about to / is running
    # them. Drives the "…" typing indicator. Reuses the proven cursor logic.
    thinking: list[str] = []
    for aid in members:
        try:
            pending = await bus.get_pending_messages(aid)
        except Exception:  # noqa: BLE001 — best-effort indicator, never fail the GET
            continue
        for pm in pending:
            ment = pm.mentions or []
            if pm.channel_id == channel_id and (aid in ment or "@everyone" in ment):
                thinking.append(aid)
                break

    return {"success": True, "channel_id": channel_id, "messages": out, "thinking": thinking}


@router.get("", response_model=TeamListResponse)
async def list_teams(request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    teams = await team_repo.list_teams_by_owner(user_id)
    enriched: list[TeamWithMembers] = []
    for t in teams:
        members = await member_repo.list_members_by_team(t.team_id)
        enriched.append(TeamWithMembers(team=t, member_agent_ids=members))
    return TeamListResponse(teams=enriched)


@router.post("", response_model=TeamOperationResponse)
async def create_team(payload: CreateTeamRequest, request: Request):
    user_id = await _user_id_for_request(request)
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")

    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.create_team(
        owner_user_id=user_id,
        name=payload.name.strip(),
        description=payload.description,
        color=payload.color,
    )
    logger.info(f"Team created: {team.team_id} by {user_id}")
    return TeamOperationResponse(success=True, team=team, message="Team created")


@router.get("/{team_id}", response_model=TeamOperationResponse)
async def get_team(team_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return TeamOperationResponse(success=True, team=team)


@router.patch("/{team_id}", response_model=TeamOperationResponse)
async def update_team(team_id: str, payload: UpdateTeamRequest, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if updates:
        await team_repo.update_team(team_id, updates)
    refreshed = await team_repo.get_team(team_id)
    return TeamOperationResponse(success=True, team=refreshed, message="Team updated")


@router.delete("/{team_id}", response_model=TeamOperationResponse)
async def delete_team(team_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    await member_repo.remove_all_members(team_id)
    await team_repo.delete_team(team_id)
    return TeamOperationResponse(success=True, message="Team deleted")


@router.post("/{team_id}/members", response_model=TeamOperationResponse)
async def add_member(team_id: str, payload: AddMemberRequest, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    agent_row = await db.get_one("agents", {"agent_id": payload.agent_id})
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent_row["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="Cannot add another user's agent")

    added = await member_repo.add_member(team_id, payload.agent_id)
    return TeamOperationResponse(
        success=True,
        message="Agent added to team" if added else "Agent already in team",
    )


@router.delete("/{team_id}/members/{agent_id}", response_model=TeamOperationResponse)
async def remove_member(team_id: str, agent_id: str, request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    team = await team_repo.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    deleted = await member_repo.remove_member(team_id, agent_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Member not found in team")
    return TeamOperationResponse(success=True, message="Member removed")
