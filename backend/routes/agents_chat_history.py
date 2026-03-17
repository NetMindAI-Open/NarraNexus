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

from fastapi import APIRouter, Query
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import InstanceRepository
from xyz_agent_context.schema import (
    EventInfo,
    NarrativeInfo,
    ChatHistoryResponse,
    ClearHistoryResponse,
    SimpleChatMessage,
    SimpleChatHistoryResponse,
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
    user_id: Optional[str] = Query(None, description="Optional user ID to filter"),
    event_limit: int = Query(default=50, description="Maximum number of recent events to return (0=unlimited)")
):
    """
    Get all Narratives and Events as chat history

    Improved query logic: not only relies on narrative_info.actors, but also
    supplements via ChatModule instance lookup. This ensures chat history is
    returned even if Narrative actors are set incorrectly.
    """
    logger.info(f"Getting chat history for agent: {agent_id}, user: {user_id}")

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
            logger.info(f"Found {len(chat_instances)} ChatModule instances for user={user_id}")

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
        logger.error(f"Error getting chat history: {e}")
        return ChatHistoryResponse(success=False, error=str(e))


@router.delete("/{agent_id}/history", response_model=ClearHistoryResponse)
async def clear_conversation_history(
    agent_id: str,
    user_id: Optional[str] = Query(None, description="Optional user ID to filter")
):
    """
    Clear Agent's conversation history

    Search logic:
    1. Query all Narratives under the specified agent_id
    2. Parse narrative_info JSON field, check if actors list contains user_id
    3. Delete matching Narratives and all associated Events
    """
    logger.info(f"Clearing history for agent: {agent_id}, user: {user_id}")

    try:
        db_client = await get_db_client()

        narratives = await db_client.get("narratives", filters={"agent_id": agent_id})

        if not narratives:
            logger.info("No narratives found to delete")
            return ClearHistoryResponse(success=True)

        logger.info(f"Found {len(narratives)} narratives")

        # Filter by user_id
        narrative_ids_to_delete = []

        if user_id:
            for narrative in narratives:
                narrative_id = narrative.get("narrative_id")
                if not narrative_id:
                    continue

                narrative_info = _parse_json_field(narrative.get("narrative_info"), None)
                if narrative_info is None:
                    continue

                actors = narrative_info.get("actors", [])
                if any(actor.get("id") == user_id for actor in actors):
                    narrative_ids_to_delete.append(narrative_id)
                    logger.debug(f"Narrative {narrative_id} contains user {user_id}")
        else:
            narrative_ids_to_delete = [
                n.get("narrative_id") for n in narratives
                if n.get("narrative_id")
            ]

        if not narrative_ids_to_delete:
            logger.info(f"No matching records to delete (agent_id={agent_id}, user_id={user_id})")
            return ClearHistoryResponse(success=True)

        logger.info(f"Will delete {len(narrative_ids_to_delete)} narratives: {narrative_ids_to_delete}")

        # Delete Events and Narratives within a transaction
        events_deleted = 0
        narratives_deleted = 0

        async with db_client.transaction():
            for narrative_id in narrative_ids_to_delete:
                count = await db_client.delete("events", filters={"narrative_id": narrative_id})
                events_deleted += count
                logger.debug(f"Deleted {count} events for narrative_id={narrative_id}")

            for narrative_id in narrative_ids_to_delete:
                count = await db_client.delete("narratives", filters={"narrative_id": narrative_id})
                narratives_deleted += count

        logger.info(f"Deleted {narratives_deleted} narratives and {events_deleted} events")

        return ClearHistoryResponse(
            success=True,
            narrative_ids_deleted=narrative_ids_to_delete,
            narratives_count=narratives_deleted,
            events_count=events_deleted,
        )

    except Exception as e:
        logger.error(f"Error clearing history: {e}")
        return ClearHistoryResponse(success=False, error=str(e))


@router.get("/{agent_id}/simple-chat-history", response_model=SimpleChatHistoryResponse)
async def get_simple_chat_history(
    agent_id: str,
    user_id: str = Query(..., description="User ID"),
    limit: int = Query(default=20, description="Maximum number of messages to return (recent N rounds)")
):
    """
    Get simplified chat history between user and Agent

    Queries directly from ChatModule instances, without relying on Narratives.
    Finds all ChatModule instances via agent_id + user_id to retrieve chat records.
    """
    logger.info(f"Getting simple chat history for agent: {agent_id}, user: {user_id}, limit: {limit}")

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

        logger.info(f"Found {len(chat_instances)} active ChatModule instances for agent={agent_id}, user={user_id}")

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

                    for msg in messages:
                        meta_data = msg.get("meta_data", {})
                        working_source = meta_data.get("working_source", "chat")
                        role = msg.get("role", "unknown")

                        # Frontend chat history filter: only show chat-type messages
                        if working_source != "chat":
                            continue

                        timestamp = meta_data.get("timestamp") or msg.get("created_at")

                        all_messages.append({
                            "role": role,
                            "content": msg.get("content", ""),
                            "timestamp": timestamp,
                            "narrative_id": narrative_id,
                            "instance_id": instance.instance_id,
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

        # Limit return count
        total_count = len(all_messages)
        if limit > 0 and total_count > limit:
            all_messages = all_messages[-limit:]

        response_messages = [
            SimpleChatMessage(
                role=msg["role"],
                content=msg["content"],
                timestamp=msg.get("timestamp"),
                narrative_id=msg.get("narrative_id")
            )
            for msg in all_messages
        ]

        logger.info(f"Returning {len(response_messages)} messages (total: {total_count})")

        return SimpleChatHistoryResponse(
            success=True,
            messages=response_messages,
            total_count=total_count
        )

    except Exception as e:
        logger.error(f"Error getting simple chat history: {e}")
        import traceback
        traceback.print_exc()
        return SimpleChatHistoryResponse(success=False, error=str(e))
