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

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.repository import TeamRepository, TeamMemberRepository
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
