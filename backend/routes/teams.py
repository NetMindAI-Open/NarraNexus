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

import mimetypes
import shutil

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils.mime_sniff import sniff_mime_type
from xyz_agent_context.repository import TeamRepository, TeamMemberRepository
from xyz_agent_context.repository.user_repository import UserRepository
from xyz_agent_context.message_bus.local_bus import LocalMessageBus
from xyz_agent_context.message_bus.attachments import (
    load_bus_attachment_meta,
    resolve_shared_file_for_user,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)
from xyz_agent_context.schema.attachment_schema import derive_category_from_mime
from xyz_agent_context.utils.workspace_paths import team_shared_dir
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


def _to_iso(value) -> str | None:
    """Normalise a timestamp (datetime / str / None) to an ISO 8601 string."""
    from datetime import datetime as _dt
    if value is None:
        return None
    return value.isoformat() if isinstance(value, _dt) else str(value)


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
    and/or the literal ``"@all"`` (mapped to the bus "@everyone").

    ``attachments`` are bus-attachment dicts returned by
    ``POST /{team_id}/chat/attachments`` (each carries a ``rel_path`` into the
    user's shared area); they are re-validated server-side before the send."""

    content: str
    mentions: list[str] = []
    attachments: list[dict] = []


def _resolve_default_responder(team, member_agent_ids: list[str]) -> str | None:
    """The agent that answers a team message with NO @mention.

    ``team.lead_agent_id`` if it's set and still a member; otherwise the
    earliest-joined member (``member_agent_ids`` is ordered by join time). A
    single-agent team therefore auto-responds. Returns None for an empty team.
    """
    if not member_agent_ids:
        return None
    lead = getattr(team, "lead_agent_id", None)
    if lead and lead in member_agent_ids:
        return lead
    return member_agent_ids[0]


async def _wipe_team_data(db, team, *, clear_chat: bool, clear_files: bool) -> dict:
    """Clear a team's group-chat history and/or its shared files.

    The team counterpart to ``wipe_agent_data``: it clears the collaboration
    *surface* (the room's messages + the ``_shared/teams/{team_id}`` folder)
    but KEEPS the team, its members, and the bus channel + membership rows so
    the room keeps working. Owner scoping is enforced by the caller.

    - clear_chat: delete ``bus_messages`` (and their ``bus_message_failures``)
      for the team room channel (``created_by='team_<id>'``).
    - clear_files: delete the on-disk ``_shared/teams/{team_id}`` dir.

    DB deletes commit first (source of truth); the disk delete runs after,
    best-effort, so a filesystem hiccup never rolls back the DB. Idempotent.
    """
    result = {"chat_messages": 0, "chat_failures": 0, "files_removed": False, "errors": []}
    marker = f"{TEAM_ROOM_OWNER_PREFIX}{team.team_id}"

    if clear_chat:
        channel = await db.get_one("bus_channels", {"created_by": marker, "channel_type": "group"})
        if channel:
            cid = channel["channel_id"]
            # One IN-subquery statement instead of pulling every message_id
            # into memory and deleting failures row-by-row (N+1 in an open
            # transaction). Bare identifiers — the raw SQL must stay portable
            # across the sqlite and MySQL dialects.
            async with db.transaction():
                failures = await db.execute(
                    "DELETE FROM bus_message_failures WHERE message_id IN "
                    "(SELECT message_id FROM bus_messages WHERE channel_id = %s)",
                    (cid,),
                    fetch=False,
                )
                result["chat_failures"] = failures if isinstance(failures, int) else 0
                result["chat_messages"] = await db.delete("bus_messages", {"channel_id": cid})

    if clear_files:
        d = team_shared_dir(team.owner_user_id, team.team_id)
        try:
            if d.exists():
                shutil.rmtree(d)
                result["files_removed"] = True
        except Exception as e:  # noqa: BLE001 — best-effort; never fail the wipe
            result["errors"].append(f"files: {e}")
            logger.warning(f"[team wipe] failed to delete shared dir {d}: {e}")

    logger.info(
        f"[team wipe] team={team.team_id} chat={clear_chat} files={clear_files} "
        f"messages={result['chat_messages']} files_removed={result['files_removed']}"
    )
    return result


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


def _sanitized_attachment(user_id: str, att: object) -> dict | None:
    """Rebuild one echoed attachment dict from server-side state only.

    The client echoes dicts from the upload endpoint, so every field is
    attacker-writable over the wire — and the dict lands verbatim in
    ``bus_messages.attachments`` and (via ``build_bus_markers``) in the team
    prompt, with ``transcript`` injected as raw text. So nothing echoed is
    trusted: the ``rel_path`` only serves to locate the file (gated to the
    sender's own shared area), then the dict is reloaded from the meta
    sidecar the upload endpoint wrote (``load_bus_attachment_meta``). No
    sidecar → minimal metadata rebuilt from disk, no transcript.
    """
    if not isinstance(att, dict):
        return None
    rel_path = att.get("rel_path")
    if not isinstance(rel_path, str) or not rel_path:
        return None
    on_disk = resolve_shared_file_for_user(user_id, rel_path)
    if on_disk is None:
        return None
    meta = load_bus_attachment_meta(user_id, rel_path)
    if meta is not None:
        return meta
    guessed, _ = mimetypes.guess_type(on_disk.name)
    mime = guessed or "application/octet-stream"
    return {
        "file_id": on_disk.stem,
        "original_name": on_disk.name,
        "mime_type": mime,
        "size_bytes": on_disk.stat().st_size,
        "category": derive_category_from_mime(mime).value,
        "rel_path": rel_path,
    }


@router.post("/{team_id}/chat/messages")
async def send_team_chat(team_id: str, payload: TeamChatSendRequest, request: Request):
    user_id = await _user_id_for_request(request)

    valid_attachments = [
        sanitized
        for att in (payload.attachments or [])
        if (sanitized := _sanitized_attachment(user_id, att)) is not None
    ]

    if not (payload.content or "").strip() and not valid_attachments:
        raise HTTPException(status_code=400, detail="Message content or an attachment is required")

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
    # No @mention → route to the team's default responder so the room never
    # goes silent. Exactly one agent is triggered; it can @-delegate from there.
    if not resolved:
        default_responder = _resolve_default_responder(team, members)
        if default_responder:
            resolved = [default_responder]
    msg_id = await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}{user_id}",
        to_channel=channel_id,
        content=payload.content.strip(),
        mentions=resolved or None,
        attachments=valid_attachments or None,
    )
    logger.info(f"Team chat: user {user_id} -> team {team_id} channel {channel_id} (mentions={resolved})")
    return {"success": True, "message_id": msg_id, "channel_id": channel_id}


@router.post("/{team_id}/chat/attachments")
async def upload_team_chat_attachment(
    team_id: str,
    request: Request,
    source: str | None = Query(
        None,
        description="'recording' = in-browser voice memo (rendered as a transcript); "
        "any other value = regular file upload. Whisper runs for all audio/* either way.",
    ),
    file: UploadFile = File(..., description="File to attach to a team chat message"),
):
    """Store a user-uploaded file into the sender's shared bus area and return a
    bus-attachment dict. The client echoes that dict back in the ``attachments``
    field of ``POST /{team_id}/chat/messages``. The file lands in
    ``{base}/{user_id}/_shared/bus_files`` so every team agent can Read it. For
    audio uploads we run Whisper (same as the single-agent path) so @mentioned
    agents receive the spoken content as text via the attachment marker."""
    from backend.config import settings as backend_settings

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    raw_bytes = await file.read()
    max_bytes = backend_settings.max_upload_bytes
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB",
        )

    mime_type = sniff_mime_type(
        raw_bytes, filename=file.filename or "", client_type=file.content_type
    )
    att = await store_bytes_into_bus(
        user_id=user_id,
        raw_bytes=raw_bytes,
        original_name=file.filename or "upload",
        mime_type=mime_type,
    )

    # Normalise source for deterministic frontend dispatch (recording vs upload).
    att["source"] = "recording" if source == "recording" else "upload"

    # Transcribe audio uploads so team agents get the words (they can't listen).
    transcription_available: bool | None = None
    if mime_type.startswith("audio/"):
        from xyz_agent_context.agent_framework.transcription import TranscriptionService

        on_disk = resolve_shared_file_for_user(user_id, att["rel_path"])
        svc = TranscriptionService.instance()
        transcription_available = await svc.is_available(user_id)
        if transcription_available and on_disk is not None:
            transcript = await svc.transcribe(
                file_path=str(on_disk),
                file_id=att["file_id"],
                agent_id="",  # team memo has no single agent; public endpoint falls back to shared resolver
                user_id=user_id,
            )
            if transcript:
                att["transcript"] = transcript
                logger.info(f"Team voice memo transcribed: file={att['file_id']} chars={len(transcript)}")

    # Persist the finished dict server-side: the send endpoint reloads it from
    # this sidecar instead of trusting the client's echoed copy.
    store_bus_attachment_meta(user_id, att)

    logger.info(f"Team chat upload: user {user_id} team {team_id} file={att['file_id']} mime={mime_type}")
    return {"success": True, "attachment": att, "transcription_available": transcription_available}


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
            "attachments": m.attachments,
            "created_at": m.created_at,
        })

    # Per-member activity for the team status view:
    #   running — the trigger is actively running it (live phase + elapsed),
    #             from the bus_agent_activity heartbeat mirror;
    #   queued  — it has an unprocessed @mention in this room but isn't running
    #             yet (poll latency / worker-slot / behind its own turn);
    #   idle    — nothing pending.
    from xyz_agent_context.message_bus import activity as bus_activity

    act_rows = {r["agent_id"]: r for r in await bus_activity.get_channel_activity(db, channel_id)}
    activity: list[dict] = []
    thinking: list[str] = []  # kept for back-compat (running+queued)
    for aid in members:
        row = act_rows.get(aid)
        if bus_activity.is_live(row):
            activity.append({
                "agent_id": aid, "status": "running",
                "phase": row.get("phase"), "tool_count": row.get("tool_count") or 0,
                "started_at": _to_iso(row.get("started_at")),
            })
            thinking.append(aid)
            continue
        queued = False
        try:
            for pm in await bus.get_pending_messages(aid):
                ment = pm.mentions or []
                if pm.channel_id == channel_id and (aid in ment or "@everyone" in ment):
                    queued = True
                    break
        except Exception:  # noqa: BLE001 — best-effort indicator, never fail the GET
            pass
        if queued:
            activity.append({"agent_id": aid, "status": "queued"})
            thinking.append(aid)
        else:
            activity.append({"agent_id": aid, "status": "idle"})

    return {
        "success": True, "channel_id": channel_id, "messages": out,
        "thinking": thinking, "activity": activity,
    }


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
    # Default-responder: a non-empty lead must be a current member; an empty
    # string clears it (back to the earliest-joined fallback). exclude_none
    # already drops a null, so "" is the wire signal for "clear".
    if "lead_agent_id" in updates:
        lead = (updates["lead_agent_id"] or "").strip()
        if lead:
            members = await TeamMemberRepository(db).list_members_by_team(team_id)
            if lead not in members:
                raise HTTPException(status_code=400, detail="lead_agent_id must be a team member")
        updates["lead_agent_id"] = lead or None
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


@router.delete("/{team_id}/data")
async def clear_team_data(
    team_id: str,
    request: Request,
    chat: bool = Query(True, description="Delete the team room's group-chat messages"),
    files: bool = Query(True, description="Delete the team's shared files (_shared/teams/{id})"),
):
    """Clear a team's collaboration data (chat and/or shared files), keeping the
    team, its members, and the bus channel. Owner-only. The team counterpart to
    the per-agent ``DELETE /{agent_id}/history``."""
    if not chat and not files:
        raise HTTPException(status_code=400, detail="Select at least one scope: chat and/or files")

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await _wipe_team_data(db, team, clear_chat=chat, clear_files=files)
    return {"success": True, **result}


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
