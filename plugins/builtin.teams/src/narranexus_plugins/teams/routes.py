"""
@file_name: routes.py
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
from datetime import datetime
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from narranexus.platform.utils import format_for_api
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.db.dialect_time import event_time_str
from narranexus.platform.utils.mime_sniff import sniff_mime_type
from narranexus.platform.repository import TeamRepository, TeamMemberRepository
from narranexus.platform.repository.user_repository import UserRepository
from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.message_bus.multipart import (
    MAX_BUS_MESSAGE_BYTES,
    OVERSIZE_REMEDY_HUMAN,
    oversize_reason,
)
from narranexus.platform.message_bus.attachments import (
    load_bus_attachment_meta,
    resolve_shared_file_for_user,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)
from narranexus.platform.message_bus.team_rooms import (
    get_or_create_team_room,
    primary_room_of,
    team_room_marker,
)
from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    placeholders as _platform_placeholders,
)
from narranexus.platform.schema.attachment_schema import derive_category_from_mime
from narranexus.platform.utils.workspace_paths import team_shared_dir
from narranexus.platform.repository.team_workspace_repository import (
    ArtifactHistoryRepository,
    TeamFileRepository,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.repository.team_bulletin_repository import TeamBulletinRepository

# Budget rules live in the core package, not here: the MCP tool enforces the
# same ceilings for agents, and a core module importing a FastAPI route to get
# them would invert the layering the architecture depends on.
from narranexus.platform.message_bus.team_bulletin import (
    BulletinLimitExceeded,
    post_bulletin_notice as _post_bulletin_notice,
    add_bulletin_entry,
    check_bulletin_budget,
    edit_bulletin_entry,
)
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
    resolve_default_responder,
    BULLETIN_MAX_ENTRIES,
    BULLETIN_MAX_ENTRY_CHARS,
    BULLETIN_MAX_TOTAL_CHARS,
    BULLETIN_SOURCE_USER,
    BULLETIN_TIER_CURRENT_TASK,
    BULLETIN_TIER_LONG_TERM,
    BulletinEntry,
    CreateTeamRequest,
    UpdateTeamRequest,
    AddMemberRequest,
    TeamWithMembers,
    TeamListResponse,
    TeamOperationResponse,
)
from narranexus.sdk.web import current_user_id


router = APIRouter()

# How many room messages one request carries. The same number in all three
# directions (newest page / catching up / scrolling back) so a reader cannot
# tell which mode produced the page they are looking at.
PAGE_SIZE = 200


async def _user_id_for_request(request: Request) -> str:
    # Unified across cloud (JWT) and local (X-User-Id header) modes —
    # auth_middleware populates request.state.user_id either way, so
    # downstream filtering is identical. See backend/auth.py for the
    # mode-specific identity source.
    return await current_user_id(request)


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


class TeamChatSendRequest(BaseModel):
    """User message into a team group chat. ``mentions`` carries agent_ids
    and/or the literal ``"@all"`` (mapped to the bus "@everyone").

    ``attachments`` are bus-attachment dicts returned by
    ``POST /{team_id}/chat/attachments`` (each carries a ``rel_path`` into the
    user's shared area); they are re-validated server-side before the send."""

    content: str
    mentions: list[str] = []
    attachments: list[dict] = []


async def _wipe_team_data(
    db,
    team,
    *,
    clear_chat: bool,
    clear_files: bool,
    clear_artifacts: bool = False,
    clear_bulletin: bool = False,
    clear_board: bool = False,
) -> dict:
    """Clear a team's group-chat history and/or its shared files.

    The team counterpart to ``wipe_agent_data``: it clears the collaboration
    *surface* (the room's messages + the ``_shared/teams/{team_id}`` folder)
    but KEEPS the team, its members, and the bus channel + membership rows so
    the room keeps working. Owner scoping is enforced by the caller.

    - clear_chat: delete ``bus_messages`` (and their ``bus_message_failures``)
      for the team room channel (``created_by='team_<id>'``).
    - clear_files: delete the on-disk ``_shared/teams/{team_id}`` dir, its
      ``team_files`` index rows, AND this team's artifacts.

      The cascade to artifacts is not an over-reach — it became correct when
      registration started REQUIRING a team artifact to live in that folder.
      An earlier version of this docstring argued the opposite, on the grounds
      that a team artifact pointed into the producer's own workspace and
      survived; that was true then and is false now. Deleting the folder now
      destroys every team artifact's content, so leaving the rows behind would
      list artifacts whose files are gone. ``ArtifactService.heal`` does not
      rescue them either — not because it is team-blind (it is not; it scopes
      itself to the team folder), but because it only ever RECONNECTS a pointer
      to a file that still exists, and this deletes the files themselves.
    - clear_artifacts: delete this team's ``instance_artifacts`` rows and their
      attribution history WITHOUT touching the folder. Used when the TEAM
      itself goes away, and available on its own for dropping the tabs while
      keeping the files.
    - clear_bulletin: delete the team's standing rules and its auto-summary.
      Deliberately its OWN scope and not folded into ``clear_chat``: the
      bulletin exists precisely because it is not chat, so wiping the
      transcript must not take the rules with it — that would recreate the
      "say it again" loop the bulletin was built to end.
    - clear_board: delete the team's work items.

    The board is a SEPARATE scope rather than part of ``clear_chat``, because
    the two answer different questions: the chat is what was said, the board is
    what is still owed. A user clearing a noisy transcript usually still wants
    to know what the team is on the hook for — and a user abandoning the work
    should not have to wipe the history to do it.

    DB deletes commit first (source of truth); the disk delete runs after,
    best-effort, so a filesystem hiccup never rolls back the DB. Idempotent.
    """
    result = {
        "chat_messages": 0,
        "chat_failures": 0,
        "files_removed": False,
        "file_rows": 0,
        "artifacts": 0,
        "bulletin_entries": 0,
        "work_items": 0,
        "errors": [],
    }
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

    if clear_board:
        result["work_items"] = await db.delete(
            "team_work_items", {"team_id": team.team_id}
        )

    if clear_files:
        # The index has to go with the bytes. A row that outlives its file is
        # worse than no row: the panel still lists it and the user only finds
        # out when the download fails. Since team artifacts are required to
        # live in this very folder, the same argument now covers them — see the
        # docstring for why the earlier reasoning stopped holding.
        result["file_rows"] = await TeamFileRepository(db).delete_by_team(team.team_id)
        clear_artifacts = True

        d = team_shared_dir(team.owner_user_id, team.team_id)
        try:
            if d.exists():
                shutil.rmtree(d)
                result["files_removed"] = True
        except Exception as e:  # noqa: BLE001 — best-effort; never fail the wipe
            result["errors"].append(f"files: {e}")
            logger.warning(f"[team wipe] failed to delete shared dir {d}: {e}")

    if clear_artifacts:
        # Attribution rows first: an orphaned history row points at an
        # artifact_id nothing can resolve. Filtered by THIS team, so private
        # work and other teams the same owner has are untouched.
        async with db.transaction():
            arts = await db.execute(
                "SELECT artifact_id FROM instance_artifacts WHERE team_id = %s",
                (team.team_id,),
                fetch=True,
            )
            await ArtifactHistoryRepository(db).delete_for_artifacts([row["artifact_id"] for row in arts or []])
            result["artifacts"] = await db.delete("instance_artifacts", {"team_id": team.team_id})

    if clear_bulletin:
        result["bulletin_entries"] = await TeamBulletinRepository(db).delete_for_team(team.team_id)

    logger.info(
        f"[team wipe] team={team.team_id} chat={clear_chat} files={clear_files} "
        f"messages={result['chat_messages']} files_removed={result['files_removed']}"
    )
    return result


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
    # The bus refuses a row over MAX_BUS_MESSAGE_BYTES at its write edge
    # (never truncates). Translate that into a 400 here rather than let the
    # ValueError surface as a 500 — the one user-typed path onto the bus.
    content_bytes = len((payload.content or "").strip().encode("utf-8"))
    if content_bytes > MAX_BUS_MESSAGE_BYTES:
        # A person is on the other end: the fact plus a human remedy, not the
        # peer tool's part_index/part_count (#389 I1).
        raise HTTPException(
            status_code=400, detail=oversize_reason(content_bytes, OVERSIZE_REMEDY_HUMAN)
        )

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
    channel_id = await get_or_create_team_room(
        db, bus, team_id=team_id, team_name=team.name, member_agent_ids=members
    )

    # Map the UI's "@all" to the bus-native "@everyone"; pass agent_ids through.
    resolved = ["@everyone" if m == "@all" else m for m in (payload.mentions or [])]
    # No @mention → route to the team's default responder so the room never
    # goes silent. Exactly one agent is triggered; it can @-delegate from there.
    routed_by = None
    if not resolved:
        default_responder = resolve_default_responder(getattr(team, "lead_agent_id", None), members)
        if default_responder:
            resolved = [default_responder]
            # Stamp WHY this mention exists. Without it the trigger cannot tell
            # a routed wake-up from the user typing that agent's name, and the
            # prompt ends up claiming "you were just @mentioned by User" when
            # nobody was mentioned at all. Guessing downstream is not available:
            # a lone mention naming the lead is exactly what a user deliberately
            # naming the lead looks like.
            routed_by = "default_responder"
    msg_id = await bus.send_message(
        from_agent=f"{USER_SENDER_PREFIX}{user_id}",
        to_channel=channel_id,
        content=payload.content.strip(),
        mentions=resolved or None,
        attachments=valid_attachments or None,
        routed_by=routed_by,
    )
    # The user's own hand-offs go on the board too.
    #
    # `record_handoffs` otherwise only sees agent→agent traffic, because that
    # is the one path routed through MessageBusTrigger. A user message reaches
    # the bus straight from here — so "@Bruno pull the numbers", ignored, left
    # no trace at all, and patrol had nothing to sweep. That is the ONE broken
    # hand-off a person actually witnesses: an agent ignoring another agent is
    # invisible to them, being ignored themselves is not.
    #
    # It also fixes the denominator. `make work-item-report` is meant to answer
    # "how many hand-offs never come back", and measuring only the half nobody
    # watches would answer a different question than the one PR #230's
    # measure-first position is asking.
    #
    # `close_delivered_errands` is deliberately NOT called here: the user is
    # never an assignee, so there is nothing of theirs to settle.
    #
    # Fire-and-forget in spirit but awaited in practice — it is bounded by
    # MAX_HANDOFFS_PER_MESSAGE, and it swallows its own failures, so it cannot
    # fail a send that already succeeded (the message id above is returned
    # either way).
    try:
        from narranexus.platform.message_bus.errand import record_handoffs

        await record_handoffs(
            db,
            team_id=team_id,
            channel_id=channel_id,
            from_agent=f"{USER_SENDER_PREFIX}{user_id}",
            # The sender is the user, who always assigns work
            # (`errand.opens_handoffs`), so the lead is irrelevant here.
            lead_agent_id=None,
            # Post-routing: a `default_responder` wake-up is the platform
            # picking someone to answer, not the user handing work to them.
            mentions=(payload.mentions and resolved) or None,
            # This is the only entrance that allows an empty body (an agent
            # reply always has text), so the shared "(untitled hand-off)"
            # fallback would surface here and nowhere else. Only the route
            # knows an attachment is what was handed over, so it says so —
            # the board is read by every member every turn, and a row that
            # names nothing is a row that costs tokens to skip.
            text=payload.content.strip() or (
                f"{len(valid_attachments)} attachment(s)"
                if valid_attachments else ""
            ),
            message_id=msg_id,
        )
    except Exception as e:  # noqa: BLE001 — the message is already delivered
        logger.warning(f"Team chat: errand bookkeeping failed for {team_id}: {e}")

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
    from narranexus.sdk.web import host_settings

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    raw_bytes = await file.read()
    max_bytes = host_settings().max_upload_bytes
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB",
        )

    mime_type = sniff_mime_type(raw_bytes, filename=file.filename or "", client_type=file.content_type)
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
        from narranexus.platform.agent_framework.llm.transcription import TranscriptionService

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
async def get_team_chat(
    team_id: str, request: Request, since: str | None = None, before: str | None = None
):
    """The room's transcript, in one of three modes.

    * no cursor — the NEWEST page. This was `get_messages(limit=200)`, which is
      `ORDER BY created_at ASC LIMIT n`: the OLDEST 200. A room that had said
      more than that opened on its first day and, because every later poll used
      `since` to walk forward from what was on screen, stayed there. Nothing
      looked broken; it showed the wrong end of the conversation forever.
    * `since` — everything after the cursor, oldest first. The 3s poll.
    * `before` — the page above the cursor. Scrolling up through history.
    """
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
    channel_id = await get_or_create_team_room(
        db, bus, team_id=team_id, team_name=team.name, member_agent_ids=members
    )

    if before:
        messages = await bus.get_messages_before(channel_id, before=before, limit=PAGE_SIZE)
    elif since:
        messages = await bus.get_messages(channel_id, since=since, limit=PAGE_SIZE)
    else:
        messages = await bus.get_recent_messages(channel_id, limit=PAGE_SIZE)

    # Resolve sender display names: agents -> agent_name; usr_<id> -> the user.
    agent_rows = await db.get_by_ids("agents", "agent_id", members) if members else []
    name_by_agent = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in agent_rows if r}
    user_name = await UserRepository(db).get_display_name(user_id)

    out = []
    for m in messages:
        is_user = m.from_agent.startswith(USER_SENDER_PREFIX)
        out.append(
            {
                "message_id": m.message_id,
                "from_agent": m.from_agent,
                "author_name": (user_name or "You") if is_user else name_by_agent.get(m.from_agent, m.from_agent),
                "is_user": is_user,
                "content": m.content,
                "attachments": m.attachments,
                # "text"/"multimodal" for ordinary messages; "system_stop" marks the
                # stop notice, which the frontend renders as a system line (from an
                # i18n key) rather than as this agent speaking.
                "msg_type": m.msg_type,
                # Whether this line is the PLATFORM narrating itself. Answered
                # here rather than by the client keeping its own copy of
                # PLATFORM_MSG_TYPES: the wire carries strings, and a type the
                # server starts sending that a client-side list does not know
                # renders as a member speaking — with an identity colour, an
                # avatar and a `team_<id>` marker for a name. That list has grown
                # from 5 to 7 in this change alone, so "remember to update the
                # other copy" is not a mechanism.
                "is_platform": (m.msg_type or "") in PLATFORM_MSG_TYPES,
                # Null for legacy messages and for any path without a monologue;
                # the panel renders those as one block.
                "segments": m.segments,
                # Turn that produced this reply (None for user messages / legacy
                # rows) — powers the per-message reasoning disclosure.
                "event_id": m.event_id,
                "created_at": format_for_api(m.created_at),
            }
        )

    activity = await _member_activity(db, bus, channel_id, members)

    return {
        "success": True,
        "channel_id": channel_id,
        "messages": out,
        "activity": activity,
        "lead_agent_id": resolve_default_responder(getattr(team, "lead_agent_id", None), members),
        # The room poll is the one feed every panel already reads, so the
        # patrol switch rides on it rather than each panel pulling the whole
        # work board to learn one boolean.
        "patrol_enabled": _patrol_enabled(team),
    }


async def _member_activity(db, bus, channel_id: str, members: list[str]) -> list[dict]:
    """Per-member live status for the team activity console.

    Four states, deliberately distinct — collapsing the last two is what made a
    wedged bus look identical to a busy one:

    * ``running``  — the trigger is running it and the heartbeat is fresh;
      carries the live phase, tool count and the turn's step timeline.
    * ``stalled``  — the row still claims running but no heartbeat has landed
      within ``ACTIVITY_STALE_SECONDS``. The turn DID start; we stopped hearing
      from it. Surfaced as its own state so the UI can say so instead of
      silently showing "queued".
    * ``queued``   — an unprocessed @mention is waiting in this room but no turn
      has started (poll latency, a busy worker slot, or the agent's own turn is
      still ahead of it). Carries how long it has been waiting.
    * ``idle``     — nothing pending. Still carries the PREVIOUS turn's step
      timeline plus when it ended, so the room can show what an agent just did.
    """
    from narranexus.platform.message_bus import activity as bus_activity

    act_rows = {r["agent_id"]: r for r in await bus_activity.get_channel_activity(db, channel_id)}
    try:
        pending = await bus.get_room_pending_summary(channel_id, members)
    except Exception as e:  # noqa: BLE001 — best-effort indicator, never fail the GET
        logger.warning(f"Team chat pending summary failed for {channel_id}: {e}")
        pending = {}

    out: list[dict] = []
    for aid in members:
        row = act_rows.get(aid)
        steps = bus_activity.parse_steps(row)
        waiting = pending.get(aid)

        if row is not None and bus_activity.is_live(row):
            status = "running"
        elif row is not None and bus_activity.is_stalled(row):
            status = "stalled"
        elif waiting:
            status = "queued"
        else:
            status = "idle"

        entry: dict = {"agent_id": aid, "status": status}
        if status in ("running", "stalled"):
            entry.update(
                {
                    "phase": row.get("phase"),
                    "tool_count": row.get("tool_count") or 0,
                    "started_at": format_for_api(row.get("started_at")),
                    # The last heartbeat. For `stalled` this is what the UI counts
                    # "no signal for N minutes" from.
                    "last_signal_at": format_for_api(row.get("updated_at")),
                    "steps": steps,
                    # The current turn's events-row id, once note_event_id() has
                    # bound it — lets the frontend fetch the full event_log via
                    # the existing event-log endpoint.
                    "event_id": row.get("event_id"),
                }
            )
        elif status == "queued":
            entry.update(
                {
                    "queued_count": waiting["count"],
                    "queued_since": format_for_api(waiting["oldest_at"]),
                }
            )
            if row is not None:
                entry["event_id"] = row.get("event_id")
            if row is not None and steps["items"]:
                # The row still holds the PREVIOUS turn's steps while this
                # one waits, so the mark survives the owner's natural next
                # move — asking again. (Not on running/stalled: `start()`
                # rewrites the steps, so the read would be a false "spoke".)
                entry["last_turn_silent"] = bus_activity.last_turn_was_silent(row)
        elif row is not None and steps["items"]:
            # Idle, but we still hold the trace of the turn it just finished.
            entry.update(
                {
                    # started_at→finished_at is where the roster's "ran Ns" comes
                    # from; omitting the start made every finished turn read "0s".
                    "started_at": format_for_api(row.get("started_at")),
                    "finished_at": format_for_api(row.get("updated_at")),
                    "steps": steps,
                    "tool_count": row.get("tool_count") or 0,
                    "event_id": row.get("event_id"),
                    # The turn ran and chose not to speak. This is where that
                    # fact lives now — it no longer gets a transcript line.
                    "last_turn_silent": bus_activity.last_turn_was_silent(row),
                }
            )
        out.append(entry)
    return out


async def _team_room_activity(
    db, team_ids: list[str], *, user_sender: str
) -> dict[str, dict]:
    """When each team's room last said something worth coming back for.

    The sidebar has no transcript — it never loads one — so "did anything happen
    while I was away" cannot be derived on the client. The unread WATERMARK is
    client-side (localStorage, per device, so the server cannot know it); what the
    server owes is the other half of the comparison: one timestamp per room.

    Two exclusions decide whether a message qualifies, and both are the
    difference between a mark users trust and one they learn to ignore:

      * the user's own message does not count, or sending a message would badge
        the room it was sent from;
      * platform lines do not count. A bulletin notice fires on the user's own
        edit and a roster notice on their own member change — badging those says
        "someone replied" when the only actor was the user.

    Deliberately does NOT create rooms. Listing teams is a read; materialising a
    channel for every team the user has never opened would mean the sidebar
    creates rooms by looking at them.

    Args:
        db: AsyncDatabaseClient.
        team_ids: Teams to report on. Empty is answered without a query — an
            ``IN ()`` with no values is a syntax error in both dialects.
        user_sender: This user's room sender id (``usr_<user_id>``).

    Returns:
        team_id -> {last_message_at, from_agent, preview}, omitting any team
        whose room does not exist or has nothing qualifying in it.
    """
    if not team_ids:
        return {}

    team_by_marker = {f"{TEAM_ROOM_OWNER_PREFIX}{tid}": tid for tid in team_ids}
    channel_rows = await db.execute(
        "SELECT channel_id, created_by FROM bus_channels "
        f"WHERE channel_type = 'group' AND created_by IN ({', '.join(['%s'] * len(team_by_marker))})",
        tuple(team_by_marker),
        fetch=True,
    )

    channel_to_team = {
        r["channel_id"]: team_by_marker[r["created_by"]]
        for r in channel_rows or []
        if r.get("created_by") in team_by_marker
    }
    if not channel_to_team:
        return {}

    # ONE query for every room, not one per room. This endpoint is polled every
    # 30s by each open tab, so a per-room query multiplies by teams AND by tabs.
    #
    # The newest QUALIFYING message per channel, filtered in SQL rather than by
    # reading a window and skipping rows: a room whose tail is all platform lines
    # would otherwise report silence while a real reply sits just behind them.
    #
    # The filter names the platform's types rather than admitting known-good
    # ones. An ordinary type added later shows up correctly under an exclusion
    # list and would be INVISIBLE under an allowlist — a talking room reading as
    # silent. `msg_type IS NULL` cannot happen (the column is NOT NULL) but is
    # kept so this filter is word-for-word the one in `message_bus_trigger`,
    # which is what `system_messages` exists to hold together.
    #
    # A MAX + self-join rather than a window function: `ROW_NUMBER() OVER` needs
    # MySQL 8, and nothing else in this codebase requires it.
    chan_ph = ", ".join(["%s"] * len(channel_to_team))

    def _where(alias: str) -> str:
        # Qualified with the alias: the outer query joins bus_messages to a
        # subquery over bus_messages, so an unqualified `channel_id` is
        # ambiguous — and SQLite says so while MySQL might not.
        return (
            f"{alias}.channel_id IN ({chan_ph}) AND {alias}.from_agent != %s "
            f"AND ({alias}.msg_type IS NULL OR "
            f"{alias}.msg_type NOT IN ({_platform_placeholders()}))"
        )

    params = (*channel_to_team, user_sender, *PLATFORM_MSG_TYPES)
    rows = await db.execute(
        f"SELECT m.channel_id, m.from_agent, m.content, m.created_at "
        f"FROM bus_messages m JOIN ("
        f"  SELECT b.channel_id AS channel_id, MAX(b.created_at) AS newest "
        f"  FROM bus_messages b WHERE {_where('b')} GROUP BY b.channel_id"
        f") t ON t.channel_id = m.channel_id AND t.newest = m.created_at "
        f"WHERE {_where('m')}",
        (*params, *params),
        fetch=True,
    )

    out: dict[str, dict] = {}
    # Raw timestamps for the cross-room comparison, function-local by
    # construction so they cannot reach the caller.
    newest: dict[str, object] = {}
    for msg in rows or []:
        team_id = channel_to_team.get(msg["channel_id"])
        if not team_id:
            continue
        # A team is one room today, but the mapping is many-to-one and nothing
        # enforces otherwise — so keep the NEWEST across a team's rooms rather
        # than whichever row the result set happened to end on. An arbitrary
        # winner would move the watermark backwards on some refreshes, which
        # reads as a mark that will not stay cleared.
        #
        # Two messages CAN share the newest instant, and then the join returns
        # both. No tie-break there: the timestamp is identical, so the watermark
        # is too, and neither of two things said in the same microsecond is the
        # more correct one to show. A guard that picked the first stood here
        # until a mutation showed nothing could tell the difference.
        # Compared on the RAW column, not on `format_for_api`'s output: that
        # truncates to whole seconds, so two rooms that spoke in the same second
        # would tie and the first row would win by accident. The formatted value
        # is for the wire only.
        #
        # Through `event_time_str`, which is what this repository already means
        # by "normalise a DATETIME cell before comparing it": sqlite hands back
        # `datetime` objects and mysql hands back strings, so a bare `str()` is
        # a fourth hand-rolled copy of a helper that exists precisely because
        # copies of it drifted. It is not equivalent either — `str(None)` is
        # `"None"`, which sorts above every real timestamp and would let a NULL
        # win forever, and a non-UTC offset compares lexicographically as if the
        # offset were part of the clock.
        raw_at = msg.get("created_at")
        # The raw value lives in a PARALLEL map, not inside the response dict:
        # putting an internal field into the object being returned and stripping
        # it at the end works only for as long as nobody adds an early return.
        if team_id in newest and event_time_str(newest[team_id]) >= event_time_str(raw_at):
            continue
        newest[team_id] = raw_at
        # Flattened and capped like the agent rows' preview right above it in the
        # same sidebar: a row is one line, and this rides in every refresh for
        # every team, so an untrimmed reply would be payload nobody can see.
        flat = " ".join((msg.get("content") or "").split())
        out[team_id] = {
            "last_message_at": format_for_api(raw_at),
            "from_agent": msg.get("from_agent") or "",
            "preview": flat[:200] if len(flat) <= 200 else flat[:200].rstrip() + "…",
        }
    return out


@router.get("", response_model=TeamListResponse)
async def list_teams(request: Request):
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team_repo = TeamRepository(db)
    member_repo = TeamMemberRepository(db)

    teams = await team_repo.list_teams_by_owner(user_id)
    activity = await _team_room_activity(
        db, [t.team_id for t in teams], user_sender=f"{USER_SENDER_PREFIX}{user_id}"
    )

    agent_names: dict[str, str] = {}
    speakers = [a["from_agent"] for a in activity.values() if a.get("from_agent")]
    if speakers:
        rows = await db.get_by_ids("agents", "agent_id", speakers)
        agent_names = {r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in rows if r}

    enriched: list[TeamWithMembers] = []
    for t in teams:
        members = await member_repo.list_members_by_team(t.team_id)
        act = activity.get(t.team_id)
        enriched.append(
            TeamWithMembers(
                team=t,
                member_agent_ids=members,
                last_message_at=(act or {}).get("last_message_at"),
                last_message_preview=(act or {}).get("preview"),
                # Resolved here rather than in the frontend: the sidebar has no
                # agent-name map for members of teams it is not showing, and a
                # raw agent_id in a preview line reads as a bug.
                last_message_author=agent_names.get((act or {}).get("from_agent") or ""),
            )
        )
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
    lead_changed = (
        "lead_agent_id" in updates
        and (updates["lead_agent_id"] or "") != (getattr(team, "lead_agent_id", None) or "")
    )
    if updates:
        await team_repo.update_team(team_id, updates)
        # The room carries its own copy of the name, and it is the copy agents
        # see: `Your Channels` renders `bus_channels.name`. Without this
        # write-back a rename lands in the UI only, and every member keeps
        # citing the old name back at the user indefinitely.
        if "name" in updates and updates["name"]:
            try:
                await db.update(
                    "bus_channels",
                    {"created_by": team_room_marker(team_id),
                     "channel_type": "group"},
                    {"name": updates["name"]},
                )
            except Exception as e:  # noqa: BLE001 — the rename itself is done
                logger.warning(
                    f"[teams] renamed {team_id} but could not update its room: {e}"
                )
    if lead_changed and updates.get("lead_agent_id"):
        # The lead is who answers when nobody is named and who patrol wakes, so
        # changing it changes who is responsible. Only announced when a lead was
        # SET: clearing it hands responsibility back to the earliest-joined
        # member by rule, which is not an event with a name to report.
        await _announce_roster(db, team_id, "lead", updates["lead_agent_id"])
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

    # Take the workspace with it. Once the team row is gone its artifacts are
    # unreachable by EVERY query path — the private surfaces exclude them
    # (team_id IS NULL), list_by_team needs a team that no longer exists, and
    # list_for_agent_context joins team_members, which the next line empties.
    # Rows nothing can ever read again are precisely the orphans the acceptance
    # criterion is about; leaving them is not "harmless clutter". The bulletin
    # and the work board are in the same position: both key on a team_id that is
    # about to stop existing, so their only readers vanish with the team.
    await _wipe_team_data(
        db,
        team,
        clear_chat=True,
        clear_files=True,
        clear_artifacts=True,
        clear_bulletin=True,
        clear_board=True,
    )
    await member_repo.remove_all_members(team_id)
    await team_repo.delete_team(team_id)
    return TeamOperationResponse(success=True, message="Team deleted")


@router.delete("/{team_id}/data")
async def clear_team_data(
    team_id: str,
    request: Request,
    chat: bool = Query(True, description="Delete the team room's group-chat messages"),
    files: bool = Query(
        True,
        description="Delete the team's shared files AND its artifacts — team artifacts live in this folder, so removing it destroys their content",
    ),
    bulletin: bool = Query(
        False,
        description="Delete the team's bulletin — its standing rules and auto-summary",
    ),
    board: bool = Query(
        False,
        description=(
            "Delete the team's work items. Off by default: an existing caller "
            "asking to clear chat did not ask to drop what the team still owes."
        ),
    ),
):
    """Clear a team's collaboration data, keeping the team, its members, and the
    bus channel. Owner-only. The team counterpart to the per-agent
    ``DELETE /{agent_id}/history``.

    Four independent scopes. Neither the bulletin nor the board is folded into
    ``chat``, and for the same reason: both are the team's standing state rather
    than its conversation, so clearing a noisy transcript must not silently take
    the rules it was given or the work it still owes.
    """
    if not any((chat, files, bulletin, board)):
        raise HTTPException(
            status_code=400,
            detail="Select at least one scope: chat, files, bulletin and/or board",
        )

    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await _wipe_team_data(
        db,
        team,
        clear_chat=chat,
        clear_files=files,
        clear_bulletin=bulletin,
        clear_board=board,
    )
    return {"success": True, **result}


# --- Team workspace (artifacts + shared files) -----------------------------
#
# The read surface for the team room's workspace panel. Both are owner-scoped
# through the same check every other team route uses: a team belongs to one
# user (the bus forbids cross-user messaging, so a team cannot span users),
# which makes the owner the tenant boundary here too.


async def _team_files(db, team_id: str) -> list[dict]:
    """A team's shared files, newest first. Thin wrapper so the route reads
    naturally and the test can call it without an HTTP client."""
    return await TeamFileRepository(db).list_by_team(team_id)


# --- Team bulletin ---------------------------------------------------------
#
# The standing rules every member loads on every team turn. Everything here
# reaches every prompt, which is why the budget below is enforced rather than
# advisory: an unbounded bulletin is an unbounded prompt on the hottest path
# the team feature has.


class CreateBulletinEntryRequest(BaseModel):
    content: str
    tier: str = BULLETIN_TIER_LONG_TERM


class UpdateBulletinEntryRequest(BaseModel):
    content: str


def _bulletin_entry_for_api(entry: BulletinEntry) -> dict[str, Any]:
    """A `BulletinEntry` as JSON: every field verbatim except the datetimes,
    which `format_for_api` converts to Z-suffixed ISO strings.

    `format_for_api` formats ONE datetime, not a dict — feeding it
    `entry.model_dump()` whole used to return `str(dict)`, a Python repr,
    as the whole entry. The frontend's filter (`e.source !== 'auto_summary'`)
    let those strings through (`undefined !== 'auto_summary'`), so the panel
    rendered one blank row per entry — `content` and `entry_id` both
    undefined, every React key undefined — not an empty panel.

    Every `datetime` value is converted, by type rather than by field name:
    a future datetime field on the model would otherwise bypass this and
    be serialised by FastAPI without the `Z` suffix the frontend's
    `new Date()` relies on.

    Top level only: `BulletinEntry` is flat today. A future nested model or
    `list[datetime]` field would be serialised by FastAPI without the `Z`
    suffix -- convert it here explicitly when such a field is added.
    """
    return {
        key: format_for_api(value) if isinstance(value, datetime) else value
        for key, value in entry.model_dump().items()
    }


@router.get("/{team_id}/bulletin")
async def list_team_bulletin(team_id: str, request: Request):
    """The team's bulletin plus its current budget usage.

    Usage rides along with the list so the panel can grey out "add" BEFORE the
    user types a rule, instead of rejecting it once written.
    """
    db, _team = await _require_team_owner(request, team_id)
    repo = TeamBulletinRepository(db)
    entries = await repo.list_for_team(team_id)
    usage = await check_bulletin_budget(repo, team_id)
    return {
        "entries": [_bulletin_entry_for_api(e) for e in entries],
        "usage": usage.model_dump(),
        "limits": {
            "max_entries": BULLETIN_MAX_ENTRIES,
            "max_entry_chars": BULLETIN_MAX_ENTRY_CHARS,
            "max_total_chars": BULLETIN_MAX_TOTAL_CHARS,
        },
    }


@router.post("/{team_id}/bulletin")
async def create_team_bulletin_entry(team_id: str, payload: CreateBulletinEntryRequest, request: Request):
    """Add a rule. Over-budget is a 400 that names the limit — never a trim."""
    db, _team = await _require_team_owner(request, team_id)
    user_id = await _user_id_for_request(request)
    tier = BULLETIN_TIER_CURRENT_TASK if payload.tier == BULLETIN_TIER_CURRENT_TASK else BULLETIN_TIER_LONG_TERM
    try:
        entry = await add_bulletin_entry(
            TeamBulletinRepository(db),
            team_id=team_id,
            content=payload.content,
            source=BULLETIN_SOURCE_USER,
            author_id=user_id,
            tier=tier,
        )
    except BulletinLimitExceeded as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True, "entry": _bulletin_entry_for_api(entry)}


@router.patch("/{team_id}/bulletin/{entry_id}")
async def update_team_bulletin_entry(
    team_id: str, entry_id: str, payload: UpdateBulletinEntryRequest, request: Request
):
    """Edit any entry — the owner's authority covers agent-written rules and
    the auto-summary alike. An agent editing its own goes through the tool."""
    db, _team = await _require_team_owner(request, team_id)
    try:
        entry = await edit_bulletin_entry(
            TeamBulletinRepository(db),
            team_id=team_id,
            entry_id=entry_id,
            content=payload.content,
        )
    except BulletinLimitExceeded as e:
        raise HTTPException(status_code=400, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="Bulletin entry not found")
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True}


@router.delete("/{team_id}/bulletin/{entry_id}")
async def delete_team_bulletin_entry(team_id: str, entry_id: str, request: Request):
    """Remove one entry. The owner may remove anything, including what an agent
    wrote and the auto-summary — that is the escape hatch that makes agent
    write access safe to grant."""
    db, _team = await _require_team_owner(request, team_id)
    repo = TeamBulletinRepository(db)
    entry = await repo.get(entry_id)
    if entry is None or entry.team_id != team_id:
        raise HTTPException(status_code=404, detail="Bulletin entry not found")
    await repo.delete(entry_id)
    await _post_bulletin_notice(db, team_id, "updated")
    return {"success": True}


@router.delete("/{team_id}/bulletin")
async def clear_team_bulletin_tier(
    team_id: str,
    request: Request,
    tier: str = Query(
        BULLETIN_TIER_CURRENT_TASK,
        description="Which tier to clear; the auto-summary belongs to no tier and survives",
    ),
):
    """Clear one tier in a single action — the "this task is done" button.

    Deliberately tier-scoped rather than a blanket clear: the standing rules
    are the part the user least wants to retype, and they are what the whole
    feature exists to stop retyping.
    """
    db, _team = await _require_team_owner(request, team_id)
    if tier not in (BULLETIN_TIER_CURRENT_TASK, BULLETIN_TIER_LONG_TERM):
        raise HTTPException(status_code=400, detail="Unknown bulletin tier")
    removed = await TeamBulletinRepository(db).delete_tier(team_id, tier)
    if removed:
        await _post_bulletin_notice(db, team_id, "cleared")
    return {"success": True, "removed": removed}


async def _require_team_owner(request: Request, team_id: str):
    """Resolve the team after checking the caller owns it. Returns (db, team)."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return db, team


@router.get("/{team_id}/artifacts")
async def list_team_artifacts(team_id: str, request: Request):
    """Artifacts owned by this team, newest first — the team room's panel.

    Not filtered by agent: the panel shows the TEAM's output whoever produced
    it. `agent_id` rides on every row so the UI can attribute each one.
    """
    db, _team = await _require_team_owner(request, team_id)
    return await ArtifactRepository(db).list_by_team(team_id)


async def _team_artifact_turns(db, team_id: str) -> dict[str, list[str]]:
    """Map each turn to the team artifacts it created or updated.

    Powers the chip under a team message: the transcript already carries each
    reply's ``event_id``, so a real key joins the two sides. Timestamps were
    never an option — one turn can register two artifacts, and two agents can
    answer in the same room at once, so proximity would attribute the wrong
    work to the wrong message.
    """
    return await ArtifactHistoryRepository(db).turns_for_team(team_id)


@router.get("/{team_id}/artifact-turns")
async def list_team_artifact_turns(team_id: str, request: Request):
    """Which artifacts each turn in this room produced (event_id → ids)."""
    db, _team = await _require_team_owner(request, team_id)
    return await _team_artifact_turns(db, team_id)


async def _authorize_team_artifact(db, team_id: str, artifact_id: str):
    """Fetch an artifact and enforce it belongs to THIS team.

    The agent-scoped route next to this one requires the caller's agent to BE
    the artifact's agent, which is precisely wrong for a team: the panel shows
    work from several members, and a teammate opening a colleague's artifact is
    the normal case. Team membership of the ARTIFACT replaces that check.

    404 rather than 403 for every refusal, matching the agent route — a
    different status would let a prober map which artifact ids exist. Owning a
    team is also not a way into the owner's private work: a NULL team_id fails
    the same comparison.
    """
    art = await ArtifactRepository(db).get_by_id(artifact_id)
    if art is None or art.team_id != team_id:
        raise HTTPException(404, "artifact not found")
    return art


@router.post("/{team_id}/artifacts/{artifact_id}/view-token")
async def mint_team_view_token(team_id: str, artifact_id: str, request: Request):
    """Mint a short-TTL view token for a team artifact's raw content.

    Authorisation happens HERE, which is where the existing design already put
    it: the token is a bearer capability for exactly one artifact, so the mint
    call is the gate. That is why the payload does not grow a team field — it
    keeps carrying the PRODUCER's agent_id, which is what the raw serving path
    resolves against, and nothing downstream has to learn about teams.

    NOT CURRENTLY CALLED, and that is a decision rather than an oversight
    (Owner, 2026-08-07). The panel renders through ArtifactRenderer, which
    mints on the agent-scoped route using the artifact's own agent_id; that
    route authorises on "does the JWT user own this agent", and every member of
    a team is an agent of the team's single owner, so it already resolves. This
    route states the accurate rule instead — the artifact belongs to THIS team,
    so owning a team is not a way into the owner's private work — and does not
    rest on teams-are-single-owner remaining true. `_authorize_team_artifact`
    is covered by tests/backend/test_team_artifact_view_token.py.
    """
    db, _team = await _require_team_owner(request, team_id)
    art = await _authorize_team_artifact(db, team_id, artifact_id)

    from narranexus.sdk.web import artifact_view_token

    token = artifact_view_token(agent_id=art.agent_id, artifact_id=artifact_id)
    return {"token": token, "raw_url": f"/api/public/artifacts/raw/{token}/"}


@router.get("/{team_id}/files")
async def list_team_files(team_id: str, request: Request):
    """Files shared into the team folder, newest first.

    This is the user-facing entry point the folder never had: `_shared/` is a
    sibling of every agent workspace, so the workspace browser cannot see it
    and the only previous way to find a file was an agent naming its path in
    chat.
    """
    db, _team = await _require_team_owner(request, team_id)
    return await _team_files(db, team_id)


async def _announce_roster(db, team_id: str, action: str, agent_id: str) -> None:
    """Tell the room its roster or lead changed.

    Only when the room EXISTS: a team whose chat has never been opened has no
    channel, and creating one to narrate a membership edit would be the tail
    wagging the dog. Best-effort — the edit itself already succeeded.
    """
    from narranexus.platform.message_bus.team_notices import post_roster_change

    channel_id = await primary_room_of(db, team_id) or ""
    if not channel_id:
        return
    agent = await db.get_one("agents", {"agent_id": agent_id})
    await post_roster_change(
        db,
        team_id=team_id,
        channel_id=channel_id,
        action=action,
        agent_name=(agent or {}).get("agent_name") or agent_id,
    )


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
    if added:
        # Who is in the room decides what @all reaches and who can answer. It
        # used to change silently, which also leaves the transcript above the
        # change reading as though the current roster wrote it.
        await _announce_roster(db, team_id, "joined", payload.agent_id)
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
    await _announce_roster(db, team_id, "left", agent_id)
    return TeamOperationResponse(success=True, message="Member removed")


# ---------------------------------------------------------------------------
# Work board
#
# Read-and-resume only. Agents maintain the board through MCP tools; the user's
# side of it is seeing what is outstanding and un-parking what a stop parked.
# Deliberately no create/delete here: a board the user edits by hand and a board
# the lead maintains would drift, and the lead is the one held responsible.
# ---------------------------------------------------------------------------


class WorkItemView(BaseModel):
    #: Task rows carry the real `item_id`; a hand-off card is keyed by the
    #: message that opened it, so it stays the same row across the 5s polls.
    item_id: str
    #: "task" = an explicit, Leader-maintained item, rendered as before.
    #: "handoff" = one @message's auto errands, collapsed. The two render
    #: differently: a task shows its title + one assignee, a hand-off shows
    #: "sender → the people still owing a reply" and drops the message text,
    #: which is the sender's words and never the assignee's.
    kind: Literal["task", "handoff"] = "task"
    title: str
    assignee_id: Optional[str] = None
    assignee_name: Optional[str] = None
    status: str
    created_at: Optional[str] = None
    #: Hand-off cards only: who sent the @message (a teammate, or the user).
    source_name: Optional[str] = None
    #: Hand-off cards only: everyone the card still tracks, resolved to names.
    assignee_names: List[str] = []
    #: The underlying rows this card stands for. One for a task; several for a
    #: collapsed hand-off, so a paused group can be resumed row by row.
    item_ids: List[str] = []
    #: Which of those rows are currently `paused`. Collapsing N rows into one
    #: card also collapsed N resume buttons into one; without the paused subset
    #: a group where only some rows are parked (a resume that failed halfway,
    #: say) aggregates to `in_progress` and the resume affordance vanishes,
    #: stranding the still-paused rows where neither patrol nor the user can
    #: reach them. The frontend offers resume whenever this is non-empty and
    #: acts on exactly these ids.
    paused_item_ids: List[str] = []


class WorkBoardResponse(BaseModel):
    success: bool
    items: List[WorkItemView]
    #: When the lead last swept the board. None = never patrolled yet.
    last_patrol_at: Optional[str] = None
    patrol_enabled: bool = True


class PatrolToggleRequest(BaseModel):
    enabled: bool


def _handoff_status(states: List[str]) -> str:
    """One status for a collapsed hand-off group.

    `stalled` is platform-derived and the whole reason the card is worth a
    glance, so any stalled member makes the card stalled. Otherwise any member
    still active keeps it in progress; a group survives on the board only while
    at least one row is unfinished, so the fall-through (everything paused) is
    the parked case the user resumes.
    """
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    if WorkItemStatus.STALLED in states:
        return WorkItemStatus.STALLED
    if any(s in WorkItemStatus.ACTIVE for s in states):
        return WorkItemStatus.IN_PROGRESS
    return WorkItemStatus.PAUSED


def _assemble_work_board(
    visible, name_by_agent: dict, *, user_name: Optional[str] = None
) -> List["WorkItemView"]:
    """Turn the raw visible rows into the cards the user sees.

    `tool` rows pass through one-to-one — those are the explicit tasks. `auto`
    rows are message-level errands: one @message that named N agents opened N
    rows, each reusing the SENDER's first line as its title. Rendered one per
    row, that put the same sentence on the board once per recipient, credited
    to people who never said it. Here they collapse to one card per source
    message: sender → the people still owing a reply, the sentence dropped.

    `user_name` resolves the one sender `name_by_agent` cannot: a user's own
    hand-off carries `created_by = "usr_<id>"` (the most common multi-@ path is
    a person naming several agents), and without it the board would render the
    opaque id as the sender's name.

    Order follows first appearance in `visible` (already `created_at, id`
    ascending), so a hand-off sits where its earliest errand would have.
    """
    from narranexus.platform.schema.team_work_schema import (
        WorkItemOrigin,
        WorkItemStatus,
    )
    from narranexus.platform.schema.team_schema import USER_SENDER_PREFIX

    def _agent_name(agent_id: Optional[str]) -> Optional[str]:
        # One fallback rule for both card kinds: a left member keeps its id
        # (matching the message list's `name_by_agent.get(x, x)`), an unclaimed
        # slot (no id) stays None so the panel renders it as "unclaimed".
        if not agent_id:
            return None
        return name_by_agent.get(agent_id) or agent_id

    def _sender_name(created_by: str) -> Optional[str]:
        if created_by.startswith(USER_SENDER_PREFIX):
            # Always the viewer — `get_work_board` gates on `_owned_team` — so
            # their display name, or the same "You" the transcript falls back to.
            return user_name or "You"
        return name_by_agent.get(created_by) or created_by or None

    cards: List[WorkItemView] = []
    groups: dict = {}          # group key -> the group's card
    member_status: dict = {}   # group key -> [each member row's status]

    for i in visible:
        if i.origin != WorkItemOrigin.AUTO:
            cards.append(WorkItemView(
                item_id=i.item_id,
                kind="task",
                title=i.title,
                assignee_id=i.assignee_id,
                assignee_name=_agent_name(i.assignee_id),
                status=i.status,
                created_at=format_for_api(i.created_at),
                item_ids=[i.item_id],
                paused_item_ids=[i.item_id] if i.status == WorkItemStatus.PAUSED else [],
            ))
            continue

        # One card per source message. A row missing its source id (should not
        # happen for an auto errand) keys on its own id, so it still collapses
        # to a single, un-merged hand-off rather than vanishing.
        key = i.source_message_id or i.item_id
        assignee_name = _agent_name(i.assignee_id)
        card = groups.get(key)
        if card is None:
            card = WorkItemView(
                item_id=key,
                kind="handoff",
                title="",
                status=i.status,  # recomputed below once the group is whole
                created_at=format_for_api(i.created_at),
                source_name=_sender_name(i.created_by),
                assignee_names=[assignee_name] if assignee_name else [],
                item_ids=[i.item_id],
                paused_item_ids=[],  # explicit fresh list; appended to below
            )
            groups[key] = card
            member_status[key] = []
            cards.append(card)
        else:
            card.item_ids.append(i.item_id)
            if assignee_name and assignee_name not in card.assignee_names:
                card.assignee_names.append(assignee_name)
        if i.status == WorkItemStatus.PAUSED:
            card.paused_item_ids.append(i.item_id)
        member_status[key].append(i.status)

    # A single stalled recipient has to win over the in_progress siblings it
    # shares a card with — done once the whole group is known.
    for card in cards:
        if card.kind == "handoff":
            card.status = _handoff_status(member_status[card.item_id])
    return cards


async def _owned_team(db, team_id: str, user_id: str):
    team = await TeamRepository(db).get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return team


@router.get("/{team_id}/work-items", response_model=WorkBoardResponse)
async def get_work_board(team_id: str, request: Request):
    """The team's board, INCLUDING parked items.

    Unlike the agent-facing tool (which lists only what still needs doing), the
    user's view has to show `paused` too — that is the state a stop leaves
    behind, and it is the user who decides whether to resume it. Hiding it
    would make a stopped task look deleted.
    """
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    team = await _owned_team(db, team_id, user_id)

    from narranexus.platform.repository.team_work_repository import (
        TeamWorkItemRepository,
    )

    # Through the repository, and filtered in SQL there: this endpoint is
    # polled every 5s by the board panel, and a long-lived team's
    # `done`/`cancelled` history only ever grows — reading all of it to throw
    # most of it away scales with age. Keeping the query in the repository also
    # keeps the dialect surface single (new raw SQL owes a MySQL test).
    visible = await TeamWorkItemRepository(db).list_visible(team_id)
    # list_members_by_team returns agent_ids, not rows (see the callers above).
    member_ids = list(await TeamMemberRepository(db).list_members_by_team(team_id))
    agent_rows = await db.get_by_ids("agents", "agent_id", member_ids) if member_ids else []
    name_by_agent = {
        r["agent_id"]: (r.get("agent_name") or r["agent_id"]) for r in agent_rows if r
    }
    # Straight off the entity: the repository already returned typed
    # `WorkItem`s, and round-tripping them through `model_dump()` would trade
    # that away for string keys a typo only breaks at request time. Auto
    # errands collapse per source message here — see `_assemble_work_board`.
    # The viewer is the owner (`_owned_team` above), so their display name is
    # the one sender name `name_by_agent` (agents only) cannot resolve — looked
    # up only when the board actually carries a user-sent hand-off, so an
    # agent-only team does not pay a users-table read on every 5s poll.
    has_user_handoff = any(
        (i.created_by or "").startswith(USER_SENDER_PREFIX) for i in visible
    )
    user_name = (
        await UserRepository(db).get_display_name(user_id) if has_user_handoff else None
    )
    items = _assemble_work_board(visible, name_by_agent, user_name=user_name)
    return WorkBoardResponse(
        success=True,
        items=items,
        last_patrol_at=format_for_api(getattr(team, "last_patrol_at", None)),
        patrol_enabled=_patrol_enabled(team),
    )


def _patrol_enabled(team) -> bool:
    """See ``team_schema.patrol_is_on`` — one implementation, two callers."""
    from narranexus.platform.schema.team_schema import patrol_is_on

    return patrol_is_on(team)


@router.post("/{team_id}/work-items/{item_id}/resume", response_model=TeamOperationResponse)
async def resume_work_item(team_id: str, item_id: str, request: Request):
    """Un-park an item a stop parked.

    The other half of the stop→pause decision: stopping means "stop running",
    not "abandon the task", so resuming has to be an explicit user action
    rather than something patrol infers.
    """
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    await _owned_team(db, team_id, user_id)

    from narranexus.platform.repository.team_work_repository import TeamWorkItemRepository
    from narranexus.platform.schema.team_work_schema import WorkItemStatus

    repo = TeamWorkItemRepository(db)
    item = await repo.get(item_id)
    if not item or item.team_id != team_id:
        raise HTTPException(status_code=404, detail="Work item not found")
    if item.status != WorkItemStatus.PAUSED:
        # Not an error: the user clicked resume on something already live.
        return TeamOperationResponse(success=True, message="Already active")
    # Back to whoever had it, or to the unclaimed pool.
    await repo.set_status(
        item_id,
        WorkItemStatus.IN_PROGRESS if item.assignee_id else WorkItemStatus.OPEN,
    )
    return TeamOperationResponse(success=True, message="Resumed")


@router.put("/{team_id}/patrol", response_model=TeamOperationResponse)
async def set_patrol_enabled(team_id: str, payload: PatrolToggleRequest, request: Request):
    """Turn the lead's patrol on or off for this team.

    Off keeps the board fully usable — it only stops the periodic sweep. A user
    who wants to track work without an agent chasing people should be able to.
    """
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    await _owned_team(db, team_id, user_id)
    await db.update("teams", {"team_id": team_id}, {"patrol_enabled": 1 if payload.enabled else 0})
    return TeamOperationResponse(success=True, message="Updated")


# ---- plugin contribution (batch 3c.2): builtin.teams provides this router through
# backend.routes; backend.main mounts it from the registry, so disabling the
# plugin (registry.json builtin_overrides) makes /api/teams/* 404 without any
# code path knowing teams exist.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("teams", lambda: RouterSpec(router, "/api/teams", tags=("Teams",))),)
