"""
@file_name: _matrix_hooks.py
@author: Bin Liang
@date: 2026-03-10
@description: MatrixModule hook implementations

Decoupling principle: MatrixModule hooks only handle Matrix's own concerns.
Social Network's batch entity extraction runs independently in its own hook —
it reads ChannelTag format without any Matrix-specific imports.

hook_data_gathering:
  - Inject Matrix identity info (matrix_id, joined rooms)
  - Scan sibling Agent contact cards
  - Optionally supplement with Registry info

hook_after_event_execution:
  - Auto mark_read for rooms that received replies
  - Update sync_token if this was a Matrix-triggered conversation
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from xyz_agent_context.schema import ContextData, HookAfterExecutionParams
from xyz_agent_context.utils import DatabaseClient

from .contact_card import ContactCard, ContactCardScanner
from ._matrix_credential_manager import MatrixCredentialManager, MatrixCredential, _sync_contact_card
from .matrix_client import NexusMatrixClient


async def matrix_hook_data_gathering(
    agent_id: str,
    db: DatabaseClient,
    ctx_data: ContextData,
    workspace_path: str = "",
    base_workspace_path: str = "",
) -> ContextData:
    """
    Inject Matrix-specific context into ctx_data.

    Only injects Matrix's own information — does NOT touch Social Network.

    Steps:
    1. Load Agent's Matrix identity (matrix_user_id, joined rooms)
    2. Scan sibling Agent contact cards (local YAML)
    3. If Registry is available, supplement with Registry info

    Args:
        agent_id: Current Agent ID
        db: Database client
        ctx_data: Context data to enrich
        workspace_path: This Agent's workspace directory
        base_workspace_path: Parent directory for all Agent workspaces

    Returns:
        Enriched ContextData
    """
    cred_mgr = MatrixCredentialManager(db)
    cred = await cred_mgr.get_credential(agent_id)

    if not cred or not cred.is_active:
        logger.debug(f"Agent {agent_id} has no active Matrix credentials, skipping")
        return ctx_data

    # Sync contact card with latest agent name on every conversation turn
    if workspace_path:
        try:
            from xyz_agent_context.repository import AgentRepository
            agent = await AgentRepository(db).get_agent(agent_id)
            if agent:
                _sync_contact_card(workspace_path, agent_id, agent.agent_name or agent_id, cred)
        except Exception as e:
            logger.debug(f"Failed to sync contact card in hook: {e}")

    # 1. Matrix identity info + agent name mapping for room members
    matrix_info = {
        "matrix_user_id": cred.matrix_user_id,
        "matrix_server": cred.server_url,
    }

    # Build matrix_user_id → agent identity mapping from local DB
    agent_name_map: Dict[str, Dict[str, str]] = {}
    try:
        rows = await db.execute(
            """
            SELECT mc.agent_id, mc.matrix_user_id, a.agent_name
            FROM matrix_credentials mc
            LEFT JOIN agents a ON mc.agent_id = a.agent_id
            WHERE mc.is_active = TRUE
            """,
            fetch=True,
        )
        for row in rows:
            mid = row.get("matrix_user_id", "")
            if mid:
                agent_name_map[mid] = {
                    "agent_id": row.get("agent_id", ""),
                    "agent_name": row.get("agent_name", "Unknown"),
                    "matrix_user_id": mid,
                }
    except Exception as e:
        logger.debug(f"Failed to build agent name map: {e}")

    matrix_info["agent_directory"] = agent_name_map

    # Try to load joined rooms list (with member names resolved)
    client = NexusMatrixClient(server_url=cred.server_url)
    try:
        rooms = await client.list_rooms(api_key=cred.api_key)
        if rooms:
            enriched_rooms = []
            for r in rooms:
                room_data = {
                    "room_id": r.get("room_id", ""),
                    "name": r.get("name", ""),
                }
                # Try to resolve members for this room
                try:
                    members = await client.get_room_members(
                        api_key=cred.api_key,
                        room_id=r.get("room_id", ""),
                    )
                    if members:
                        resolved = []
                        for m in members:
                            mid = m.get("user_id", "") if isinstance(m, dict) else str(m)
                            info = agent_name_map.get(mid, {})
                            resolved.append({
                                "matrix_user_id": mid,
                                "agent_id": info.get("agent_id", ""),
                                "agent_name": info.get("agent_name", mid),
                            })
                        room_data["members"] = resolved
                except Exception:
                    pass  # Non-critical
                enriched_rooms.append(room_data)
            matrix_info["joined_rooms"] = enriched_rooms
    except Exception as e:
        logger.debug(f"Failed to list Matrix rooms for context injection: {e}")
    finally:
        await client.close()

    ctx_data.extra_data["matrix_info"] = matrix_info

    # 2. Scan sibling Agent contact cards
    if base_workspace_path:
        try:
            siblings = await ContactCardScanner.scan_sibling_agents(
                current_agent_id=agent_id,
                base_workspace_path=base_workspace_path,
            )
            if siblings:
                ctx_data.extra_data["sibling_agents"] = siblings
        except Exception as e:
            logger.debug(f"Failed to scan sibling agent cards: {e}")

    return ctx_data


async def matrix_hook_after_event_execution(
    params: HookAfterExecutionParams,
    db: DatabaseClient,
) -> None:
    """
    Post-execution cleanup for Matrix.

    Only handles Matrix's own concerns — entity extraction is done by
    SocialNetworkModule independently.

    Steps:
    1. If Agent called matrix_send_message → auto mark_read for that room
    2. If this was a Matrix-triggered conversation → update sync_token

    Args:
        params: Hook execution parameters
        db: Database client
    """
    # Check if this was a Matrix-triggered execution
    ws = params.working_source
    ws_value = ws.value if hasattr(ws, "value") else str(ws)
    if ws_value != "matrix":
        return

    agent_id = params.agent_id

    # Look up credential
    cred_mgr = MatrixCredentialManager(db)
    cred = await cred_mgr.get_credential(agent_id)
    if not cred:
        return

    # Extract room IDs that were replied to (from tool calls in trace)
    replied_rooms = set()
    if params.trace and params.trace.agent_loop_response:
        for response in params.trace.agent_loop_response:
            if hasattr(response, "tool_name") and response.tool_name == "matrix_send_message":
                # Try to extract room_id from tool args
                if hasattr(response, "tool_input") and isinstance(response.tool_input, dict):
                    room_id = response.tool_input.get("room_id")
                    if room_id:
                        replied_rooms.add(room_id)

    # Mark rooms as read
    if replied_rooms:
        client = NexusMatrixClient(server_url=cred.server_url)
        try:
            for room_id in replied_rooms:
                await client.mark_read(api_key=cred.api_key, room_id=room_id)
                logger.debug(f"Auto marked room {room_id} as read for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Failed to auto mark_read: {e}")
        finally:
            await client.close()
