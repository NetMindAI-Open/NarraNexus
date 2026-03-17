"""
@file_name: agents_awareness.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Awareness routes

Provides endpoints for:
- GET /{agent_id}/awareness - Get Agent self-awareness
- PUT /{agent_id}/awareness - Update Agent self-awareness
"""

import uuid

from fastapi import APIRouter
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.repository import InstanceRepository
from xyz_agent_context.repository import InstanceAwarenessRepository
from xyz_agent_context.schema import AwarenessResponse, AwarenessUpdateRequest
from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus


router = APIRouter()


async def _ensure_awareness_instance(agent_id: str) -> str:
    """
    Ensure an AwarenessModule instance exists for the Agent, create one if not

    Returns:
        instance_id
    """
    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)
    instances = await instance_repo.get_by_agent(
        agent_id=agent_id,
        module_class="AwarenessModule"
    )

    if instances:
        return instances[0].instance_id

    logger.info(f"No AwarenessModule instance found, creating one for agent: {agent_id}")
    instance_id = f"aware_{uuid.uuid4().hex[:8]}"
    new_instance = ModuleInstanceRecord(
        instance_id=instance_id,
        module_class="AwarenessModule",
        agent_id=agent_id,
        is_public=True,
        status=InstanceStatus.ACTIVE,
        description="Agent self-awareness module instance"
    )
    await instance_repo.create_instance(new_instance)
    logger.info(f"Created new instance: {instance_id}")
    return instance_id


@router.get("/{agent_id}/awareness", response_model=AwarenessResponse)
async def get_agent_awareness(agent_id: str):
    """
    Get Agent self-awareness information

    Queries data from instance_awareness table (via AwarenessModule's instance_id).
    Creates an AwarenessModule instance automatically if none exists.
    """
    logger.info(f"Getting awareness for agent: {agent_id}")

    try:
        db_client = await get_db_client()
        instance_id = await _ensure_awareness_instance(agent_id)

        awareness_data = await db_client.get_one(
            "instance_awareness",
            filters={"instance_id": instance_id}
        )

        if awareness_data:
            return AwarenessResponse(
                success=True,
                awareness=awareness_data.get("awareness"),
                create_time=format_for_api(awareness_data.get("created_at")),
                update_time=format_for_api(awareness_data.get("updated_at")),
            )
        else:
            return AwarenessResponse(
                success=False,
                error=f"Awareness data not found for agent: {agent_id}"
            )

    except Exception as e:
        logger.error(f"Error getting awareness: {e}")
        return AwarenessResponse(success=False, error=str(e))


@router.put("/{agent_id}/awareness", response_model=AwarenessResponse)
async def update_agent_awareness(agent_id: str, request: AwarenessUpdateRequest):
    """
    Update Agent self-awareness information

    Updates data in instance_awareness table (via AwarenessModule's instance_id).
    Creates an AwarenessModule instance automatically if none exists.
    """
    logger.info(f"Updating awareness for agent: {agent_id}")
    logger.info(f"  → Request awareness content (first 100 chars): {request.awareness[:100] if request.awareness else 'None'}...")

    try:
        db_client = await get_db_client()
        instance_id = await _ensure_awareness_instance(agent_id)
        logger.info(f"  → Using instance_id: {instance_id}")

        # Update instance_awareness table
        awareness_repo = InstanceAwarenessRepository(db_client)
        success = await awareness_repo.upsert(instance_id, request.awareness)
        logger.info(f"  → Upsert result: {success}")

        if success:
            awareness_data = await db_client.get_one(
                "instance_awareness",
                filters={"instance_id": instance_id}
            )
            logger.info(f"  → Fetched awareness_data: {awareness_data is not None}")
            if awareness_data:
                logger.info(f"  → Fetched awareness (first 100 chars): {str(awareness_data.get('awareness', ''))[:100]}...")

            return AwarenessResponse(
                success=True,
                awareness=awareness_data.get("awareness") if awareness_data else request.awareness,
                create_time=format_for_api(awareness_data.get("created_at")) if awareness_data else None,
                update_time=format_for_api(awareness_data.get("updated_at")) if awareness_data else None,
            )
        else:
            logger.error(f"  → Upsert failed for instance_id: {instance_id}")
            return AwarenessResponse(success=False, error="Failed to update awareness")

    except Exception as e:
        logger.error(f"Error updating awareness: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return AwarenessResponse(success=False, error=str(e))
