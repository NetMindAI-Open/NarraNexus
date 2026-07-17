"""
@file_name: agents_chat_history.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Chat History routes

Provides endpoints for:
- GET /{agent_id}/chat-history - Get all Narratives and Events
- DELETE /{agent_id}/history - Clear conversation history
- GET /{agent_id}/simple-chat-history - Get simplified chat message list
"""

import json
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from backend.auth import resolve_current_user_id
from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import InstanceRepository
from xyz_agent_context.narrative.wipe_service import wipe_agent_data
from xyz_agent_context.schema import (
    EventInfo,
    NarrativeInfo,
    ChatHistoryResponse,
    ClearHistoryResponse,
    SimpleChatMessage,
    SimpleChatHistoryResponse,
    EventLogToolCall,
    EventLogTimelineEntry,
    EventLogResponse,
)
from xyz_agent_context.schema.api_schema import InstanceInfo


router = APIRouter()


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
    user_id = await resolve_current_user_id(request)
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

    user_id = await resolve_current_user_id(request)
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
    offset: int = Query(default=0, description="Number of recent messages to skip (for pagination from newest)")
):
    """
    Get simplified chat history between user and Agent. Identity from auth_middleware.

    Queries directly from ChatModule instances, without relying on Narratives.
    Finds all ChatModule instances via agent_id + user_id to retrieve chat records.
    """
    user_id = await resolve_current_user_id(request)
    logger.debug(f"Getting simple chat history for agent: {agent_id}, user: {user_id}, limit: {limit}")

    try:
        db_client = await get_db_client()
        instance_repo = InstanceRepository(db_client)

        all_messages: List[Dict[str, Any]] = []

        all_instances = await instance_repo.get_by_agent_and_user(
            agent_id=agent_id,
            user_id=user_id,
            include_public=False
        )
        chat_instances = [
            inst for inst in all_instances
            if inst.module_class == "ChatModule"
            and inst.status not in ("cancelled", "archived")
        ]

        logger.debug(f"Found {len(chat_instances)} active ChatModule instances for agent={agent_id}, user={user_id}")

        for instance in chat_instances:
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

                    # working_source values that represent a real human
                    # typing into a chat UI (as opposed to an IM channel
                    # trigger, scheduled job, or peer-agent call). For
                    # these, the "user" side is a genuine user message
                    # and MUST be surfaced in the history view.
                    #
                    # "manyfold" was missing here originally — Manyfold's
                    # OpenAI-compat endpoint feeds the user input via the
                    # same `input_content` field as CHAT does, so the row
                    # is a real user message; filtering it out left the
                    # native UI showing only the agent's replies (the
                    # symptom Bin哥 reported 2026-05-26).
                    _USER_FACING_SOURCES = ("chat", "manyfold")

                    for msg in messages:
                        meta_data = msg.get("meta_data", {})
                        working_source = meta_data.get("working_source", "chat")
                        role = msg.get("role", "unknown")

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
                        # ``send_message_to_user_directly`` during the IM
                        # turn (the "tell owner about this important
                        # thing" path the iron rules carve out), the
                        # writer stashes that content on
                        # ``meta_data.owner_notify_content``. We surface
                        # it verbatim as a real reply (message_type left
                        # untouched) so the owner DOES see the
                        # important notification while routine IM
                        # chatter stays hidden.
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

        # Paginate: messages are sorted oldest→newest; slice from the end
        # offset=0, limit=20 → last 20 messages (most recent)
        # offset=20, limit=20 → messages 20-40 from the end (older page)
        total_count = len(all_messages)
        if offset > 0:
            end_idx = max(0, total_count - offset)
            start_idx = max(0, end_idx - limit)
            all_messages = all_messages[start_idx:end_idx]
        elif limit > 0 and total_count > limit:
            all_messages = all_messages[-limit:]

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
            for msg in all_messages
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
async def get_event_log_detail(agent_id: str, event_id: str):
    """
    Get event log detail (thinking + tool calls) for a specific event.

    Used by the frontend to lazily load reasoning and tool call details
    for historical chat messages. The event_log is already stored in the
    events table during Step 4 of the pipeline.
    """
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

        # Extract tool calls: pair each tool_call with the next tool_output
        tool_calls: List[EventLogToolCall] = []
        entries_content = [
            entry.get("content", {}) if isinstance(entry.get("content"), dict) else entry
            for entry in event_log
        ]

        i = 0
        while i < len(entries_content):
            entry = entries_content[i]
            if isinstance(entry, dict) and entry.get("type") == "tool_call":
                tool_name = entry.get("tool_name", "unknown")
                tool_input = entry.get("arguments", {})

                # Look ahead for matching tool_output
                tool_output = None
                if i + 1 < len(entries_content):
                    next_entry = entries_content[i + 1]
                    if isinstance(next_entry, dict) and next_entry.get("type") == "tool_output":
                        tool_output = next_entry.get("output")
                        i += 1  # Skip the tool_output entry

                tool_calls.append(EventLogToolCall(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=tool_output,
                ))
            i += 1

        # Build the time-ordered timeline view. We walk event_log entries
        # in their original order — that order IS the agent's actual
        # think→tool→think→tool→reply rhythm, and we don't want to lose
        # it the way the grouped thinking/tool_calls fields above do.
        # Consecutive thinking deltas are concatenated into one entry so
        # the UI doesn't render 50 tiny italic blocks.
        timeline: List[EventLogTimelineEntry] = []
        pending_thinking: List[str] = []

        def _flush_thinking():
            if pending_thinking:
                timeline.append(EventLogTimelineEntry(
                    type="thinking",
                    content="".join(pending_thinking),
                ))
                pending_thinking.clear()

        for entry in event_log:
            content = entry.get("content", {}) if isinstance(entry.get("content"), dict) else entry
            if not isinstance(content, dict):
                continue
            ctype = content.get("type")
            if ctype == "thinking":
                txt = content.get("content", "")
                if txt:
                    pending_thinking.append(txt)
            elif ctype == "tool_call":
                _flush_thinking()
                # Some legacy stored entries carry a reply_via tag on the
                # send_message tool — preserve it so the historical Reply
                # block can render the "helper_llm fallback" badge.
                timeline.append(EventLogTimelineEntry(
                    type="tool_call",
                    tool_name=content.get("tool_name", "unknown"),
                    tool_input=content.get("arguments", {}) or {},
                    reply_via=(content.get("details") or {}).get("reply_via"),
                ))
            elif ctype == "tool_output":
                _flush_thinking()
                timeline.append(EventLogTimelineEntry(
                    type="tool_output",
                    tool_name=content.get("tool_name", "unknown"),
                    tool_output=content.get("output"),
                ))
            elif ctype in ("native_output", "agent_response"):
                _flush_thinking()
                txt = content.get("content", "")
                if txt:
                    timeline.append(EventLogTimelineEntry(
                        type="native_output",
                        content=txt,
                    ))
            # Other types (progress markers, etc.) intentionally skipped.
        _flush_thinking()

        return EventLogResponse(
            success=True,
            event_id=event_id,
            thinking=thinking,
            tool_calls=tool_calls,
            timeline=timeline,
        )

    except Exception as e:
        logger.exception(f"Error getting event log detail: {e}")
        return EventLogResponse(success=False, event_id=event_id, error=str(e))
