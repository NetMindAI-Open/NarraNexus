"""
@file_name: routes.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Chat History routes

Provides endpoints for:
- GET /{agent_id}/chat-history - Get all Narratives and Events
- DELETE /{agent_id}/history - Clear conversation history
- GET /{agent_id}/simple-chat-history - Get simplified chat message list
- GET /{agent_id}/event-log/{event_id} - Get one event's timeline
- POST /{agent_id}/chat-history/by-instance - MCP seam twin of get_chat_history
"""

import json
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from narranexus.sdk.web import current_user_id
from narranexus.sdk.web import require_agent_owner
from narranexus_plugins.chat_module import fetch_chat_history
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils import format_for_api
from narranexus.platform.repository import InstanceRepository, AgentRepository
from narranexus.platform.narrative.wipe_service import wipe_agent_data
from narranexus.platform.schema.hook_schema import BUS_PRODUCED_SOURCES
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from narranexus.platform.schema import (
    EventInfo,
    NarrativeInfo,
    ChatHistoryResponse,
    ClearHistoryResponse,
    SimpleChatMessage,
    SimpleChatHistoryResponse,
    EventLogToolCall,
    EventLogMeta,
    EventLogResponse,
)
from narranexus.platform.schema.api_schema import InstanceInfo
from narranexus_plugins.chat_module.chat_history_timeline import build_event_timeline


router = APIRouter()

#: working_source values produced by MessageBusTrigger for peer-agent (A2A)
#: and team turns. The Activity Log surfaces these — and ONLY these — from a
#: peer-scoped instance (see get_simple_chat_history). Sourced from the enum so
#: a new bus transport updates every consumer at once (see chat_module).
_A2A_TEAM_SOURCES = BUS_PRODUCED_SOURCES

#: working_source values that represent a real human typing into a chat UI (as
#: opposed to an IM channel trigger, scheduled job, or peer-agent call). For
#: these the "user" side is a genuine user message and MUST be surfaced.
#: "manyfold" — Manyfold's OpenAI-compat endpoint feeds the user input via the
#: same input_content field as CHAT, so its row is a real user message (the
#: 2026-05-26 symptom was the native UI showing only the agent's replies).
_USER_FACING_SOURCES = ("chat", "manyfold")

#: user_id prefixes MessageBusTrigger writes as the SENDER of a peer/team turn:
#: the peer agent id (A2A, ``agent_``), a team room's own platform posts
#: (``team_``, patrol/notice), and a HUMAN speaking in a team room
#: (``usr_``, teams.py bus.send_message). All three land the turn in a
#: ChatModule instance keyed to that sender id. A real user's own user_id is
#: bare (no ``usr_`` prefix — cloud is a 32-hex NetMind code), so ``usr_`` here
#: matches only the bus-sender form and does NOT pull other users' private
#: chats. Filtering the peer set on these prefixes keeps other users' private
#: chats out of the read ENTIRELY; the row-level working_source gate below
#: stays as the second, fail-closed net (covers the local-mode edge where an
#: externally-supplied X-User-Id could itself start with ``usr_``).
_PEER_SCOPE_PREFIXES = ("agent_", TEAM_ROOM_OWNER_PREFIX, USER_SENDER_PREFIX)

#: Upper bound on peer/team ChatModule instances the Activity Log fans out to
#: per request (owner-only path, polled every 12s). Generous vs any realistic
#: paging depth so a normal owner is never truncated; a hard ceiling so cost
#: cannot grow without bound with the agent's social surface.
_MAX_PEER_ACTIVITY_INSTANCES = 200


def _parse_timestamp(ts: str) -> datetime:
    """Parse various timestamp formats into datetime objects"""
    if not ts:
        return datetime.min
    try:
        ts_normalized = ts.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(ts_normalized)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (ValueError, AttributeError):
            pass

        for fmt in [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
        ]:
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                return dt
            except ValueError:
                continue

        logger.warning(f"Unable to parse timestamp: {ts}")
        return datetime.min
    except Exception as e:
        logger.warning(f"Error parsing timestamp {ts}: {e}")
        return datetime.min


def _parse_json_field(value: Any, default: Any) -> Any:
    """Parse database fields that may be JSON strings"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


def _drop_phantom_event_twins(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter the legacy duplicate `events` rows out of a replay set.

    Until 2026-08-05 step 4.4 authored a COPY of the turn's event row into
    every auxiliary narrative that narrative selection had returned (see
    `step_4_persist_results` §4.4). The copy was written at the end of the run
    from the in-memory event, whose `event_log` is never back-filled, so each
    multi-narrative turn left 1-2 rows carrying an empty `event_log` and the
    primary's `final_output` verbatim. Replaying them shows the same exchange
    up to three times, ordered by the run's finish time — an already-answered
    question surfacing beneath newer ones.

    The producer is gone, but the rows are in every deployed database and
    cannot be deleted (铁律 #6 — no destructive migration), so they are
    filtered here.

    A row is dropped only when all three hold, which is the copy's exact
    signature and nothing else:
      1. its `event_log` is empty (the copy never had one);
      2. it carries a non-empty `final_output` (there is content to duplicate);
      3. another row in the set has the SAME turn input and the SAME
         `final_output` *and* a non-empty `event_log` — i.e. the original.

    So a genuine turn that died before its first step (log-less, no logged
    sibling) survives, and a re-ask survives because it has its own log and
    its own reply. Both cases exist in real data: `evt_1e33fa7004d54298` is a
    real turn (1 tool call, a real reply) that nonetheless persisted an empty
    `event_log`, and it is correctly kept.

    **Known residue, deliberately not filtered.** A copy whose ORIGINAL also
    has an empty `event_log` is indistinguishable by content — condition (3)
    finds no logged sibling, so it survives (four such rows exist locally,
    e.g. `evt_188705c45f7349ab`). They all carry an EMPTY `final_output`, so
    they add no repeated text and no monologue fragment — the two things the
    user actually reported. `started_at IS NOT NULL` would separate them (only
    the primary gets one, from `RunRecorder`), but using it would also drop a
    genuine crashed re-ask whose reply happens to match an earlier turn's, and
    hiding a real turn is worse than showing an empty row. Rejected on that
    basis; revisit only with a stronger primary marker.

    Args:
        rows: Raw `events` rows for one replay, in display order.

    Returns:
        The same rows, in the same order, minus the copies.
    """
    def _identity(row: Dict[str, Any]) -> tuple:
        env = _parse_json_field(row.get("env_context"), {})
        user_input = env.get("input", "") if isinstance(env, dict) else ""
        return (
            row.get("trigger") or "",
            row.get("trigger_source") or "",
            user_input,
            row.get("final_output") or "",
        )

    logged_identities = {
        _identity(row) for row in rows
        if _parse_json_field(row.get("event_log"), [])
    }
    if not logged_identities:
        return list(rows)

    kept: List[Dict[str, Any]] = []
    for row in rows:
        is_logless = not _parse_json_field(row.get("event_log"), [])
        has_output = bool(row.get("final_output"))
        if is_logless and has_output and _identity(row) in logged_identities:
            logger.debug(
                f"chat-history: dropping phantom event twin {row.get('event_id')} "
                f"(narrative={row.get('narrative_id')})"
            )
            continue
        kept.append(row)
    return kept


@router.get("/{agent_id}/chat-history", response_model=ChatHistoryResponse)
async def get_chat_history(
    agent_id: str,
    request: Request,
    event_limit: int = Query(default=50, description="Maximum number of recent events to return (0=unlimited)")
):
    """
    Get all Narratives and Events as chat history. Identity from auth_middleware.

    Improved query logic: not only relies on narrative_info.actors, but also
    supplements via ChatModule instance lookup. This ensures chat history is
    returned even if Narrative actors are set incorrectly.

    History: ``user_id`` was an "Optional[str] = Query()" filter — that
    semantically reads as "let me filter by anyone I name", which is the
    cross-user-read class of bug. Identity is now strictly the caller.
    """
    user_id = await current_user_id(request)
    logger.debug(f"Getting chat history for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()
        instance_repo = InstanceRepository(db_client)

        narrative_ids: List[str] = []
        narrative_map: Dict[str, Any] = {}

        # ===== Method 1: Find associated Narratives via ChatModule instance =====
        if user_id:
            all_instances = await instance_repo.get_by_agent_and_user(
                agent_id=agent_id,
                user_id=user_id,
                include_public=False
            )
            chat_instances = [inst for inst in all_instances if inst.module_class == "ChatModule"]
            logger.debug(f"Found {len(chat_instances)} ChatModule instances for user={user_id}")

            for inst in chat_instances:
                links = await db_client.get(
                    "instance_narrative_links",
                    filters={"instance_id": inst.instance_id}
                )
                for link in links:
                    nar_id = link.get("narrative_id")
                    if nar_id and nar_id not in narrative_ids:
                        narrative_ids.append(nar_id)

            # Load detailed info for these Narratives
            valid_narrative_ids = []
            for nar_id in narrative_ids:
                nar_row = await db_client.get_one("narratives", {"narrative_id": nar_id})
                if nar_row:
                    valid_narrative_ids.append(nar_id)
                    narrative_info = _parse_json_field(nar_row.get("narrative_info"), {})

                    actors = narrative_info.get("actors", [])
                    if not any(a.get("id") == user_id for a in actors):
                        actors.append({"id": user_id, "type": "user"})

                    narrative_map[nar_id] = {
                        "narrative_id": nar_id,
                        "name": narrative_info.get("name", f"Conversation with {user_id}"),
                        "description": narrative_info.get("description", ""),
                        "current_summary": narrative_info.get("current_summary", ""),
                        "actors": actors,
                        "created_at": format_for_api(nar_row.get("created_at")),
                        "updated_at": format_for_api(nar_row.get("updated_at")),
                    }

            narrative_ids = valid_narrative_ids

        # ===== Method 2: Fallback to narrative_info.actors-based query (legacy data compat) =====
        if not narrative_ids:
            narratives_raw = await db_client.get(
                "narratives",
                filters={"agent_id": agent_id},
                order_by="created_at ASC"
            )

            if not narratives_raw:
                return ChatHistoryResponse(success=True)

            for narrative in narratives_raw:
                narrative_id = narrative.get("narrative_id")
                if not narrative_id:
                    continue

                narrative_info = _parse_json_field(narrative.get("narrative_info"), None)
                if narrative_info is None:
                    continue

                if user_id:
                    actors = narrative_info.get("actors", [])
                    if not any(actor.get("id") == user_id for actor in actors):
                        continue

                narrative_ids.append(narrative_id)
                narrative_map[narrative_id] = {
                    "narrative_id": narrative_id,
                    "name": narrative_info.get("name", ""),
                    "description": narrative_info.get("description", ""),
                    "current_summary": narrative_info.get("current_summary", ""),
                    "actors": narrative_info.get("actors", []),
                    "created_at": format_for_api(narrative.get("created_at")),
                    "updated_at": format_for_api(narrative.get("updated_at")),
                }

        if not narrative_ids:
            return ChatHistoryResponse(success=True)

        # Query Instances associated with each Narrative
        for narrative_id in narrative_ids:
            links = await db_client.get(
                "instance_narrative_links",
                filters={"narrative_id": narrative_id, "link_type": "active"}
            )
            instance_ids = [link.get("instance_id") for link in links if link.get("instance_id")]

            instances = []
            for instance_id in instance_ids:
                instance_rows = await db_client.get(
                    "module_instances",
                    filters={"instance_id": instance_id}
                )
                if instance_rows:
                    inst = instance_rows[0]
                    status = inst.get("status", "active")
                    if status in ("cancelled", "archived"):
                        continue

                    config = _parse_json_field(inst.get("config"), {})
                    deps = _parse_json_field(inst.get("dependencies"), [])

                    instances.append(InstanceInfo(
                        instance_id=inst.get("instance_id", ""),
                        module_class=inst.get("module_class", ""),
                        description=inst.get("description", ""),
                        status=status,
                        dependencies=deps,
                        config=config,
                        created_at=format_for_api(inst.get("created_at")),
                        user_id=inst.get("user_id")
                    ))

            if narrative_id in narrative_map:
                narrative_map[narrative_id]["instances"] = instances

        # Query all Events
        events_raw = []
        for narrative_id in narrative_ids:
            narrative_events = await db_client.get(
                "events",
                filters={"narrative_id": narrative_id},
                order_by="created_at ASC"
            )
            events_raw.extend(narrative_events)

        events_raw.sort(key=lambda e: e.get("created_at", ""))

        # Legacy duplicate rows first — dropping them before the trim below
        # means `event_limit` buys real turns, not copies.
        events_raw = _drop_phantom_event_twins(events_raw)

        # Trim to most recent N events
        if event_limit > 0 and len(events_raw) > event_limit:
            events_raw = events_raw[-event_limit:]

        # Build response
        narratives = [NarrativeInfo(**narrative_map[nid]) for nid in narrative_ids]

        events = []
        for event in events_raw:
            event_id = event.get("event_id") or event.get("id")
            narrative_id = event.get("narrative_id")
            event_log = _parse_json_field(event.get("event_log"), [])

            events.append(EventInfo(
                event_id=event_id,
                narrative_id=narrative_id,
                narrative_name=narrative_map.get(narrative_id, {}).get("name"),
                trigger=event.get("trigger", ""),
                trigger_source=event.get("trigger_source", ""),
                user_id=event.get("user_id"),
                final_output=event.get("final_output", ""),
                created_at=format_for_api(event.get("created_at")),
                event_log=event_log,
            ))

        return ChatHistoryResponse(
            success=True,
            narratives=narratives,
            events=events,
            narrative_count=len(narratives),
            event_count=len(events),
        )

    except Exception as e:
        logger.exception(f"Error getting chat history: {e}")
        return ChatHistoryResponse(success=False, error=str(e))


@router.delete("/{agent_id}/history", response_model=ClearHistoryResponse)
async def clear_conversation_history(
    agent_id: str,
    request: Request,
    conversations: bool = Query(True, description="Delete conversation history / narratives / trajectories / sessions"),
    memory: bool = Query(True, description="Delete the agent's learned long-term memory (memory_*, artifacts)"),
):
    """
    Clear an agent's data for the calling owner, scoped by ``conversations``
    and ``memory`` flags (the frontend's checkbox dialog maps to these).

    This delegates to ``wipe_agent_data`` which — unlike the old handler —
    also removes the on-disk narrative markdown and trajectory files. Those
    files are the real long-memory surface (the DB is rebuilt from them on
    restart), so clearing the DB alone left the agent still remembering.

    Identity comes from the session only; ``?user_id=`` is rejected and the
    caller must own the agent (memory_* is agent-scoped — a non-owner wipe
    would destroy the owner's memory), otherwise 404.
    """
    if "user_id" in request.query_params:
        raise HTTPException(
            status_code=400,
            detail="user_id query param not accepted; identity comes from the session",
        )
    if not conversations and not memory:
        raise HTTPException(
            status_code=400,
            detail="Select at least one scope: conversations and/or memory",
        )

    user_id = await current_user_id(request)
    db_client = await get_db_client()

    # Ownership: 404 masks both "no such agent" and "not yours".
    agent_row = await db_client.get_one("agents", {"agent_id": agent_id})
    if not agent_row or agent_row.get("created_by") != user_id:
        raise HTTPException(status_code=404, detail="Agent not found")

    logger.info(
        f"Clearing agent={agent_id} user={user_id} "
        f"conversations={conversations} memory={memory}"
    )

    try:
        result = await wipe_agent_data(
            db_client, agent_id, user_id,
            clear_conversations=conversations, clear_memory=memory,
        )
        return ClearHistoryResponse(
            success=True,
            scopes=result.scopes,
            narrative_ids_deleted=result.narrative_ids,
            narratives_count=result.narratives_count,
            events_count=result.events_count,
            event_stream_count=result.event_stream_count,
            chat_memory_count=result.chat_memory_count,
            chat_instances_count=result.chat_instances_count,
            agent_messages_count=result.agent_messages_count,
            bus_messages_count=result.bus_messages_count,
            bus_failures_count=result.bus_failures_count,
            # The IM record is the largest content class and the frontend renders
            # it directly, so a wipe that silently stopped clearing it has to be
            # distinguishable from a successful one IN THE RESPONSE — that
            # indistinguishability is how the missing delete survived until a
            # review found it.
            inbox_threads_count=result.inbox_threads_count,
            inbox_thread_messages_count=result.inbox_thread_messages_count,
            report_memory_count=result.report_memory_count,
            instance_links_count=result.instance_links_count,
            memory_rows_count=result.memory_rows_count,
            artifacts_count=result.artifacts_count,
            disk_markdown_removed=result.disk_markdown_removed,
            disk_trajectories_removed=result.disk_trajectories_removed,
            session_removed=result.session_removed,
            disk_errors=result.disk_errors,
        )
    except Exception as e:
        logger.exception(f"Error clearing agent data: {e}")
        return ClearHistoryResponse(success=False, error=str(e))


@router.get("/{agent_id}/simple-chat-history", response_model=SimpleChatHistoryResponse)
async def get_simple_chat_history(
    agent_id: str,
    request: Request,
    limit: int = Query(default=20, description="Maximum number of messages to return"),
    offset: int = Query(default=0, description="Number of recent messages to skip (for pagination from newest)"),
    include: Literal["chat", "activity", "all"] = Query(
        default="all",
        description="Which stream to return: 'chat' (conversation only), "
                    "'activity' (Activity Log only), or 'all' (both merged). "
                    "Case-sensitive (FastAPI-validated).",
    ),
):
    """
    Get simplified chat history between user and Agent. Identity from auth_middleware.

    Queries directly from ChatModule instances, without relying on Narratives.
    Finds all ChatModule instances via agent_id + user_id to retrieve chat records.

    The conversation and Activity Log are two independent streams the frontend
    renders in separate tabs. They must paginate independently — a busy team /
    A2A agent produces an unbounded activity stream that would otherwise eat a
    shared ``limit`` and starve the conversation tab. ``include`` selects the
    stream so each tab gets its own ``limit`` / ``offset`` / ``total_count``.
    """
    user_id = await current_user_id(request)
    want_activity = include != "chat"
    logger.debug(
        f"Getting simple chat history for agent: {agent_id}, user: {user_id}, "
        f"limit: {limit}, include: {include}"
    )

    try:
        db_client = await get_db_client()
        instance_repo = InstanceRepository(db_client)

        all_messages: List[Dict[str, Any]] = []

        # Owner check gates the peer/team activity pull below. Resolved via
        # AgentRepository directly (not the _ownership seam) ON PURPOSE: this
        # endpoint is user-scoped, not owner-gated, and must FAIL OPEN to the
        # caller's own history on a db hiccup — resolve_owner returning None
        # (db failure) / "" (unknown) both leave is_owner False, keeping prior
        # behaviour, whereas _ownership._deny_reason raises 503 on None and
        # would turn a transient blip into a 500 for the whole panel. Only
        # computed when an activity stream is actually requested.
        is_owner = False
        if want_activity:
            owner = await AgentRepository(db_client).resolve_owner(agent_id)
            is_owner = bool(owner) and owner == user_id

        all_instances = await instance_repo.get_by_agent_and_user(
            agent_id=agent_id,
            user_id=user_id,
            include_public=False
        )
        # (instance, peer_scoped) — owner-scoped instances carry the full
        # conversation; peer-scoped ones contribute A2A/team ACTIVITY only.
        scoped_instances = [
            (inst, False)
            for inst in all_instances
            if inst.module_class == "ChatModule"
            and inst.status not in ("cancelled", "archived")
        ]

        # Activity Log visibility for A2A / team turns. MessageBusTrigger runs
        # those turns with user_id = sender_agent_id (the peer / team id, not
        # the owner — message_bus_trigger.py), so they live in ChatModule
        # instances the owner-scoped query above never returns. Pull the
        # agent's other ChatModule instances too, but ONLY for the owner and
        # ONLY the peer/team scopes (user_id prefix). Filtering here — not just
        # at the row level below — keeps every OTHER user's private chat with a
        # shared/public agent out of the read entirely (no memory JSON parsed
        # for them). The message loop then surfaces ONLY the background a2a /
        # message_bus rows as the second, fail-closed net.
        if is_owner:
            agent_instances = await instance_repo.get_by_agent(
                agent_id=agent_id, module_class="ChatModule"
            )
            peer_instances = [
                inst
                for inst in agent_instances
                if inst.user_id != user_id
                and inst.user_id
                and inst.user_id.startswith(_PEER_SCOPE_PREFIXES)
                and inst.status not in ("cancelled", "archived")
            ]
            # Bound the fan-out: this runs under a 12s poll while the inner tab
            # is open, and reads + parses each instance's whole memory. Cap at
            # the most-recent N (get_by_agent is created_at DESC) and LOG when
            # truncated rather than silently dropping older activity. The cap is
            # well above HISTORY_PAGE_SIZE * any realistic paging depth, so it
            # does not change total_count for a normal owner. The real long-term
            # fix is an indexed activity source (events) instead of scanning
            # each instance's memory JSON.
            if len(peer_instances) > _MAX_PEER_ACTIVITY_INSTANCES:
                logger.info(
                    f"Activity Log fan-out capped for agent={agent_id}: "
                    f"{len(peer_instances)} peer/team instances → newest "
                    f"{_MAX_PEER_ACTIVITY_INSTANCES}"
                )
                peer_instances = peer_instances[:_MAX_PEER_ACTIVITY_INSTANCES]
            scoped_instances.extend((inst, True) for inst in peer_instances)

        logger.debug(
            f"Assembling chat history for agent={agent_id}, user={user_id}: "
            f"{len(scoped_instances)} instances (owner={is_owner})"
        )

        for instance, peer_scoped in scoped_instances:
            try:
                memory_row = await db_client.get_one(
                    "instance_json_format_memory_chat",
                    filters={"instance_id": instance.instance_id}
                )

                if memory_row and memory_row.get("memory"):
                    memory_data = _parse_json_field(memory_row["memory"], {})
                    messages = memory_data.get("messages", [])

                    links = await db_client.get(
                        "instance_narrative_links",
                        filters={"instance_id": instance.instance_id},
                        limit=1
                    )
                    narrative_id = links[0].get("narrative_id") if links else None

                    for msg in messages:
                        meta_data = msg.get("meta_data", {})
                        working_source = meta_data.get("working_source", "chat")
                        role = msg.get("role", "unknown")

                        # Peer-scoped (A2A / team) instances contribute
                        # ACTIVITY only: their a2a / message_bus rows, which the
                        # block below collapses to a compact marker. Any other
                        # source (a user-facing chat, an IM channel) belonging
                        # to a non-owner scope is dropped here, never surfaced.
                        if peer_scoped and working_source not in _A2A_TEAM_SOURCES:
                            continue

                        # For non-user-facing sources (job/lark/slack/
                        # telegram/message_bus/etc), only show assistant
                        # messages — the "user" side is the trigger
                        # prompt, not something a human typed.
                        if (
                            working_source not in _USER_FACING_SOURCES
                            and role != "assistant"
                        ):
                            continue

                        timestamp = meta_data.get("timestamp") or msg.get("created_at")
                        message_type = meta_data.get("message_type", "chat")

                        # Privacy guard for IM channels: agent replies sent
                        # via platform tools (tg_cli sendMessage, slack_cli
                        # chat.postMessage, lark_cli +messages-send) live in
                        # the same instance memory as chat replies so the
                        # agent's NEXT turn can still see them (long-term
                        # memory). But surfacing the raw IM reply text to
                        # the owner's chat panel mixes two contexts:
                        #   (a) "owner ↔ agent direct chat"
                        #   (b) "agent ↔ third party on IM"
                        # The frontend chat panel is for (a); routine IM
                        # chatter should stay on the IM platform. We
                        # replace the content with a one-line activity
                        # marker AND override ``message_type`` to
                        # ``"activity"`` so the frontend renders it
                        # compactly (small centered italic line) rather
                        # than as a full agent reply bubble — see
                        # ``ChatPanel.tsx`` ``if item.messageType ===
                        # 'activity'`` branch.
                        #
                        # Carve-out: when the agent explicitly called
                        # ``send_message_to_user_directly`` / ``notify_owner``
                        # during the turn (the "tell owner about this important
                        # thing" path the iron rules carve out), the writer
                        # stashes that content on
                        # ``meta_data.owner_notify_content``. We surface it
                        # VERBATIM so the owner DOES see the notification while
                        # routine IM/peer chatter stays hidden. Which tab it
                        # lands in depends on scope — see the branch below.
                        content = msg.get("content", "")
                        # Privacy-collapse rule applies only to truly-IM
                        # / background sources where the assistant reply
                        # was meant for a third party, not for the owner
                        # viewing this chat panel. Manyfold's assistant
                        # reply IS the user-facing answer, so leave it
                        # alone.
                        if (
                            working_source not in _USER_FACING_SOURCES
                            and role == "assistant"
                        ):
                            owner_notify = meta_data.get("owner_notify_content", "")
                            if owner_notify:
                                content = owner_notify
                                # Owner scope: the agent's own IM turn notifying
                                # its owner — keep it in the conversation tab
                                # (message_type stays 'chat'), the long-standing
                                # behaviour. Peer scope (A2A/team): the
                                # conversation tab never reads peer instances and
                                # the activity stream keeps only
                                # message_type=='activity', so a 'chat'-typed row
                                # would vanish from BOTH tabs. Mark it 'activity'
                                # (content stays verbatim, NOT collapsed) so the
                                # Activity Log shows the agent's report.
                                if peer_scoped:
                                    message_type = "activity"
                            else:
                                content = f"Background activity ({working_source})"
                                # Force compact rendering on the frontend.
                                # Without this the row keeps the writer's
                                # default message_type and renders as a
                                # full chat bubble — observed 2026-05-13.
                                message_type = "activity"

                        all_messages.append({
                            "role": role,
                            "content": content,
                            "timestamp": timestamp,
                            "narrative_id": narrative_id,
                            "instance_id": instance.instance_id,
                            "working_source": working_source,
                            "message_type": message_type,
                            "event_id": meta_data.get("event_id"),
                            # The seeded first-run greeting: the client folds the
                            # same greeting into its session on the first send and
                            # must recognise this row as that greeting (the two
                            # texts differ once the client localises its copy).
                            "bootstrap": bool(meta_data.get("bootstrap")),
                            "attachments": msg.get("attachments"),
                            "_sort_key": timestamp or ""
                        })

                    logger.debug(
                        f"Loaded {len(messages)} messages from instance {instance.instance_id}"
                    )
            except Exception as e:
                logger.warning(f"Failed to load chat history from instance {instance.instance_id}: {e}")

        # Sort by time
        all_messages.sort(key=lambda m: _parse_timestamp(m.get("_sort_key", "")))

        if all_messages:
            logger.debug(f"First message timestamp: {all_messages[0].get('_sort_key', 'N/A')}")
            logger.debug(f"Last message timestamp: {all_messages[-1].get('_sort_key', 'N/A')}")

        # Select the requested stream BEFORE paginating so the conversation and
        # the Activity Log each get their own budget. An activity row is one the
        # collapse logic above marked message_type == "activity"; everything
        # else is a conversation row. Without this split the two tabs share one
        # `limit` and a busy team/A2A agent's activity flood empties the
        # conversation tab (and polling evicts already-rendered chat rows).
        if include == "chat":
            stream = [m for m in all_messages if m.get("message_type") != "activity"]
        elif include == "activity":
            stream = [m for m in all_messages if m.get("message_type") == "activity"]
        else:
            stream = all_messages

        # Paginate: messages are sorted oldest→newest; slice from the end
        # offset=0, limit=20 → last 20 messages (most recent)
        # offset=20, limit=20 → messages 20-40 from the end (older page)
        # total_count is per-stream so the frontend's loadMore termination and
        # offset arithmetic track the stream it is actually paging.
        total_count = len(stream)
        if offset > 0:
            end_idx = max(0, total_count - offset)
            start_idx = max(0, end_idx - limit)
            stream = stream[start_idx:end_idx]
        elif limit > 0 and total_count > limit:
            stream = stream[-limit:]

        response_messages = [
            SimpleChatMessage(
                role=msg["role"],
                content=msg["content"],
                timestamp=msg.get("timestamp"),
                narrative_id=msg.get("narrative_id"),
                working_source=msg.get("working_source"),
                message_type=msg.get("message_type"),
                event_id=msg.get("event_id"),
                attachments=msg.get("attachments"),
            )
            for msg in stream
        ]

        logger.debug(f"Returning {len(response_messages)} messages (total: {total_count})")

        return SimpleChatHistoryResponse(
            success=True,
            messages=response_messages,
            total_count=total_count
        )

    except Exception as e:
        logger.exception(f"Error getting simple chat history: {e}")
        import traceback
        traceback.print_exc()
        return SimpleChatHistoryResponse(success=False, error=str(e))


@router.get("/{agent_id}/event-log/{event_id}", response_model=EventLogResponse)
async def get_event_log_detail(agent_id: str, event_id: str, request: Request):
    """
    Get event log detail (thinking + tool calls) for a specific event.

    Used by the frontend to lazily load reasoning and tool call details
    for historical chat messages. The event_log is already stored in the
    events table during Step 4 of the pipeline.
    """
    # The event log carries the turn's thinking and every tool call with its
    # input/output — owner-only, like the sibling /events/{event_id} route
    # (2026-10-28: this one had no auth at all).
    await require_agent_owner(request, agent_id)
    logger.debug(f"Getting event log detail: agent_id={agent_id}, event_id={event_id}")

    try:
        db_client = await get_db_client()

        event_row = await db_client.get_one(
            "events",
            {"event_id": event_id, "agent_id": agent_id}
        )

        if not event_row:
            return EventLogResponse(
                success=False,
                event_id=event_id,
                error="Event not found"
            )

        event_log = _parse_json_field(event_row.get("event_log"), [])

        # Extract thinking: concatenate streaming deltas into coherent blocks.
        # Each thinking_delta is stored as a separate step in event_log.
        # Consecutive thinking entries are part of the same block (concatenate directly).
        # When interrupted by other step types (tool_call, etc.), start a new block with \n\n.
        thinking_blocks: List[str] = []
        current_block: List[str] = []
        for entry in event_log:
            content = entry.get("content", {})
            if isinstance(content, dict) and content.get("type") == "thinking":
                thinking_text = content.get("content", "")
                if thinking_text:
                    current_block.append(thinking_text)
            else:
                # Non-thinking entry: flush current block if any
                if current_block:
                    thinking_blocks.append("".join(current_block))
                    current_block = []
        # Flush remaining block
        if current_block:
            thinking_blocks.append("".join(current_block))

        thinking = "\n\n".join(thinking_blocks) if thinking_blocks else None

        # Extract tool calls, pairing each with its output. Pair by
        # tool_call_id when the stored entries carry it — with PARALLEL
        # calls every call lands before any output and the outputs return
        # in completion order, so the positional "next entry" rule crosses
        # the wires (same rule, same reason, as response_processor's
        # tool-call reconstruction). Positional look-ahead survives only as
        # the fallback for legacy rows without ids.
        tool_calls: List[EventLogToolCall] = []
        entries_content = [
            entry.get("content", {}) if isinstance(entry.get("content"), dict) else entry
            for entry in event_log
        ]

        # ONE shared index for "which output belongs to which call", read by
        # BOTH views below (the grouped tool_calls and the timeline). The two
        # views answer the same question over the same rows; separate indexes
        # kept drifting one patch apart, and a drift means the endpoint
        # contradicts itself (timeline says read_file, the tool card says
        # web_search). First occurrence wins for duplicate ids (legacy rows).
        outputs_by_id: Dict[str, Any] = {}
        call_names: Dict[str, str] = {}
        for entry in entries_content:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("tool_call_id")
            if not entry_id:
                continue
            if entry.get("type") == "tool_output" and entry_id not in outputs_by_id:
                outputs_by_id[entry_id] = entry.get("output")
            elif entry.get("type") == "tool_call" and entry_id not in call_names:
                # Record even a known-empty name: lookups must distinguish
                # "this call's name is empty" from "id never seen".
                call_names[entry_id] = entry.get("tool_name") or ""

        def _pairs_positionally(next_entry: Any, call_id: Optional[str]) -> bool:
            """May this adjacent output pair with the call positionally?
            An id-carrying output is reserved for id pairing — unless the
            call itself has no id, in which case adjacency is all we have.
            (Pointer advancement stays with the caller: the grouped view
            consumes the row, the timeline never does.)"""
            return (
                isinstance(next_entry, dict)
                and next_entry.get("type") == "tool_output"
                and (not next_entry.get("tool_call_id") or not call_id)
            )

        i = 0
        while i < len(entries_content):
            entry = entries_content[i]
            if isinstance(entry, dict) and entry.get("type") == "tool_call":
                tool_name = entry.get("tool_name") or ""
                tool_input = entry.get("arguments", {})
                call_id = entry.get("tool_call_id")

                tool_output = None
                if call_id and call_id in outputs_by_id:
                    tool_output = outputs_by_id[call_id]
                elif i + 1 < len(entries_content) and _pairs_positionally(
                    entries_content[i + 1], call_id
                ):
                    tool_output = entries_content[i + 1].get("output")
                    i += 1  # Skip the tool_output entry

                tool_calls.append(EventLogToolCall(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=tool_output,
                ))
            i += 1

        timeline = build_event_timeline(entries_content, call_names)

        meta = await _build_event_meta(db_client, event_row, tool_call_count=len(tool_calls))

        return EventLogResponse(
            success=True,
            event_id=event_id,
            thinking=thinking,
            tool_calls=tool_calls,
            timeline=timeline,
            meta=meta,
        )

    except Exception as e:
        logger.exception(f"Error getting event log detail: {e}")
        return EventLogResponse(success=False, event_id=event_id, error=str(e))


# Cap for meta.input_text — a bus payload or a rendered job prompt can be
# hundreds of KB; the card only needs enough to read what the run was about.
_META_INPUT_CAP = 4000


def _parse_lifecycle_dt(value) -> Optional[datetime]:
    """Parse a lifecycle timestamp column (datetime on MySQL, str on SQLite)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


async def _build_event_meta(db_client, event_row: Dict[str, Any], *, tool_call_count: int) -> EventLogMeta:
    """
    Assemble the run-level header for the activity card.

    Workflow:
    1. Input: env_context.input (what the agent actually received), capped.
    2. Lifecycle: state + started/finished + derived duration (Phase C
       columns; legacy rows leave them NULL -> fields stay None).
    3. Costs: aggregate cost_records by event_id — distinct models, token
       sums, total USD. No rows -> total_cost_usd stays None so the UI can
       hide the chip instead of showing a misleading $0.

    Args:
        db_client: Active AsyncDatabaseClient.
        event_row: The raw events row.
        tool_call_count: Number of tool calls extracted from event_log.

    Returns:
        EventLogMeta ready for the response.
    """
    env_context = _parse_json_field(event_row.get("env_context"), {})
    input_text = None
    if isinstance(env_context, dict):
        raw_input = env_context.get("input")
        if isinstance(raw_input, str) and raw_input.strip():
            input_text = raw_input[:_META_INPUT_CAP]

    started = _parse_lifecycle_dt(event_row.get("started_at"))
    finished = _parse_lifecycle_dt(event_row.get("finished_at"))
    duration_seconds = None
    if started and finished and finished >= started:
        duration_seconds = (finished - started).total_seconds()

    models: List[str] = []
    total_cost_usd: Optional[float] = None
    input_tokens = 0
    output_tokens = 0
    # The two cache buckets travel separately: input_tokens is only the
    # full-rate bucket, and on a cache-warm run the cache columns hold the
    # bulk of what the model actually read (same split as /costs).
    cache_read_tokens = 0
    cache_creation_tokens = 0
    cost_rows = await db_client.execute(
        "SELECT model, input_tokens, output_tokens, "
        "cache_read_input_tokens, cache_creation_input_tokens, total_cost_usd "
        "FROM cost_records WHERE event_id = %s",
        (event_row.get("event_id"),),
        fetch=True,
    )
    for row in cost_rows or []:
        model = row.get("model")
        if model and model not in models:
            models.append(model)
        input_tokens += int(row.get("input_tokens") or 0)
        output_tokens += int(row.get("output_tokens") or 0)
        cache_read_tokens += int(row.get("cache_read_input_tokens") or 0)
        cache_creation_tokens += int(row.get("cache_creation_input_tokens") or 0)
        total_cost_usd = (total_cost_usd or 0.0) + float(row.get("total_cost_usd") or 0.0)

    final_output = event_row.get("final_output")

    return EventLogMeta(
        trigger=event_row.get("trigger") or "",
        trigger_source=event_row.get("trigger_source") or "",
        input_text=input_text,
        final_output=final_output if final_output else None,
        state=event_row.get("state") or "completed",
        started_at=str(event_row.get("started_at")) if event_row.get("started_at") else None,
        finished_at=str(event_row.get("finished_at")) if event_row.get("finished_at") else None,
        duration_seconds=duration_seconds,
        models=models,
        total_cost_usd=total_cost_usd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        tool_call_count=tool_call_count,
    )


# --------------------------------------------------------------------------- MCP data-access seam twin


class ChatHistoryByInstanceBody(BaseModel):
    """Body for POST .../chat-history/by-instance — the get_chat_history tool's
    args (instance_id + limit; limit=-1 means all).

    No Field(min_length) on instance_id on purpose: a route-level 422 for an
    empty id would degrade to a DIFFERENT dict on the HttpStore side than
    DirectStore's uniform empty-history dict (fetch_chat_history returns _empty
    for any unknown/foreign/blank id), breaking byte-parity. An empty id is
    harmless — it simply matches no instance and reads as empty on both paths."""
    instance_id: str
    limit: int = 20


@router.post("/{agent_id}/chat-history/by-instance")
async def get_chat_history_by_instance(
    agent_id: str, body: ChatHistoryByInstanceBody, request: Request
) -> dict:
    """Byte-parity Http twin of the ``get_chat_history`` MCP tool: returns the
    EXACT dict the seam's DirectStore returns (both call the shared, dialect-safe
    ``fetch_chat_history``). Owner-gated; the instance is additionally scoped to
    ``agent_id`` inside the shared fn (a foreign instance reads as empty — no
    existence oracle). Distinct from GET /{agent_id}/chat-history (the frontend
    narratives+events view)."""
    await require_agent_owner(request, agent_id)
    try:
        db = await get_db_client()
    except Exception as e:  # noqa: BLE001 — fetch_chat_history never raises
        logger.warning(f"get_chat_history_by_instance failed: {e}")
        return {"success": False, "instance_id": body.instance_id, "error": str(e),
                "total_messages": 0, "messages": []}
    return await fetch_chat_history(db, agent_id, body.instance_id, body.limit)


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.chat through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_chat_history", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
